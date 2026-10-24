#!/usr/bin/env python3
"""Live PX4-SITL mono follow-me harness (garuda-follow Task 8).

SAFETY GATE (CLAUDE.md rule 5 / rule 12): this script arms the vehicle and
issues offboard velocity setpoints. Per CLAUDE.md it MUST refuse to run
without an explicit GARUDA_SIM=1, and may only ever target a SITL/simulated
endpoint.

GATE PLACEMENT -- read before changing: bench/failsafe_demo.py and
bench/offboard_demo.py put the GARUDA_SIM check at module import time, before
importing rclpy. This module puts the *same* check inside main() instead.
Reason: Task 8 also requires a pure, ROS-free, dev-PC-testable core,
`follow_tick()`, that must be importable as `from sitl_followme import
follow_tick` on a machine with no rclpy/px4_msgs installed and with no
GARUDA_SIM env var set (that's exactly how services/garuda-follow/tests/
test_sitl_followme.py runs in CI on the dev PC). Gating at import time would
make the module itself unimportable without the flag, breaking that test.

The safety property CLAUDE.md actually requires -- "this cannot command
arming/offboard setpoints without the flag" -- is fully preserved: rclpy,
px4_msgs, and the vehicle are ONLY ever touched inside main(), and main()
checks the gate as its very first action, before importing rclpy. Importing
this module (or calling follow_tick()) never talks to hardware/SITL, never
imports rclpy, and is safe with or without GARUDA_SIM set. The live entrypoint
is `main()` -- see services/garuda-follow/README or task-8-report.md for the
exact invocation.
"""
from __future__ import annotations

import math
import os
import pathlib
import sys

# Sibling modules (follow_controller, projection) resolve via whatever put
# this file's directory on sys.path (running it as __main__, or a test that
# inserts this directory -- see tests/test_sitl_followme.py). The interfaces
# package needs its own path entry, same convention as follow_controller.py
# and tracker.py in this directory.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "interfaces"))

from garuda_interfaces import Track  # noqa: E402
from follow_controller import compute, FollowConfig  # noqa: E402
from projection import project  # noqa: E402

# Synthetic world target the drone follows -- NED (x=north, y=east), relative
# to the local-position origin. This is the whole point of this harness: no
# real camera/detector is involved here. The real perception path (capture ->
# detect -> track -> follow) is proven separately in the portal
# (bench/cam_portal.py); this harness proves the OTHER half of the loop --
# compute() -> world-frame velocity -> PX4 offboard setpoint -- against a
# real PX4 SITL instance end to end.
TARGET_WORLD = (6.0, 4.0)     # (x, y) metres, NED
RATE_HZ = 20.0
STREAM_BEFORE_MODE_S = 2.0    # stream setpoints this long before arm+offboard
LOG_EVERY_S = 1.0             # log framing error / drone position this often


def body_to_world_velocity(forward_vel, vertical_vel, yaw_rate, yaw):
    """Map a body-forward FollowCommand into a PX4 NED world-frame velocity
    setpoint.

    forward_vel: m/s, +toward target, in the drone's *body*-forward axis.
    vertical_vel: m/s, +up (follow_controller convention).
    yaw_rate: rad/s, +right -- a rotation rate, frame-invariant, so it passes
        straight through as yawspeed (it is NOT rotated by yaw).
    yaw: current heading, rad, 0 = +x.

    Returns (vx, vy, vz, yawspeed) in PX4 NED world frame:
      vx = forward_vel * cos(yaw), vy = forward_vel * sin(yaw)  -- body-forward
           rotated into world by the current heading.
      vz = -vertical_vel  -- PX4 NED z is DOWN, vertical_vel is +UP, so a
           positive (climb) command must be a NEGATIVE z-velocity.
    """
    vx = forward_vel * math.cos(yaw)
    vy = forward_vel * math.sin(yaw)
    vz = -vertical_vel
    yawspeed = yaw_rate
    return vx, vy, vz, yawspeed


def quaternion_to_yaw(q):
    """Extract yaw (rad, 0 = +x, PX4 NED convention) from a PX4-order
    quaternion [w, x, y, z].

    Standard yaw-from-quaternion formula for the Z-axis (yaw) component of a
    body attitude quaternion in NED:
      yaw = atan2(2*(w*z + x*y), 1 - 2*(y*y + z*z))
    """
    w, x, y, z = q[0], q[1], q[2], q[3]
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def build_setpoint_fields(result, hold_alt):
    """Build the (position, velocity, yawspeed) fields for a MIXED per-axis
    PX4 TrajectorySetpoint.

    Live-bench finding (2026-07-31): a pure-velocity setpoint with vz=0 never
    commands a climb (follow_tick's vz is always 0 -- projection.py is 2D, no
    altitude framing model), so the quad just sat on the ground and could
    never translate. Fix: split axes -- altitude is POSITION-controlled
    (position z = -hold_alt, NED down -> PX4 holds/climbs to that altitude),
    horizontal motion + yaw rate are VELOCITY-controlled (vx, vy, yawspeed
    from follow_tick). velocity[2] is left NaN -- that axis is owned by
    position[2] above, and follow_tick's vz is a 2D no-op anyway, so it must
    not be commanded through velocity.z.

    On a HOLD/invalid follow_tick result (no pose yet / target lost), this
    still returns the altitude hold (position z = -hold_alt) plus
    velocity=[0, 0, NaN] and yawspeed=0 -- i.e. hover in place at altitude,
    not "stop commanding altitude too". This preserves the safety semantics
    (lost target -> hover, never chase) while airborne.
    """
    position = [float("nan"), float("nan"), -hold_alt]
    if result["valid"]:
        velocity = [result["vx"], result["vy"], float("nan")]
        yawspeed = result["yawspeed"]
    else:
        velocity = [0.0, 0.0, float("nan")]
        yawspeed = 0.0
    return position, velocity, yawspeed


def follow_tick(drone_x, drone_y, drone_z, drone_yaw, target_world, cfg):
    """Pure, ROS-free follow-me control tick -- no rclpy/px4_msgs required.

    1. Projects `target_world` into the drone's camera via
       projection.project(target_world, (drone_x, drone_y), drone_yaw) ->
       bbox | None.
    2. Builds a Track-like from the bbox (fresh, i.e. time_since_update=0 --
       this is a synthetic per-tick projection, not a persistent tracker
       with staleness of its own), or leaves the target as None if the
       projection reports the target out of frame / behind the camera.
    3. Runs it through follow_controller.compute() to get a body-forward
       FollowCommand (yaw_rate, forward_vel, vertical_vel, valid, reason).
    4. On an invalid command (no target / low confidence / stale / implausible
       size) returns a HOLD: all-zero velocity, valid=False, and the reason.
    5. On a valid command, maps it into PX4 NED world-frame velocity via
       body_to_world_velocity().

    drone_z is accepted (it mirrors the VehicleOdometry fields the live node
    reads each tick) but is not used by this pipeline today:
    projection.project only models horizontal bearing/range -- its `vfov`
    parameter is documented as unused -- so this pipeline does not model
    vertical framing/altitude error, and vertical_vel out of compute() is
    always 0 while projection.py stays 2D. Honest scope note (CLAUDE.md rule
    9), not a bug: see task-8-report.md.

    Returns a dict with the required keys {"vx", "vy", "vz", "yawspeed",
    "valid", "reason"} plus two diagnostic-only extras used by main()'s
    per-second logging: "bbox" (the projected bbox or None) and
    "framing_error" (the same abs(center-err) + abs(size-err) metric used by
    tests/test_convergence.py, or None when there is no target).
    """
    bbox = project(target_world, (drone_x, drone_y), drone_yaw)

    if bbox is None:
        track = None
        framing_error = None
    else:
        track = Track(id=1, class_id=0, label="person", bbox=bbox,
                      confidence=0.9, hits=1, age=1, time_since_update=0)
        bx, by, bw, bh = bbox
        cx = bx + bw / 2.0
        framing_error = abs(cx - 0.5) + abs(cfg.desired_height_frac - bh)

    cmd = compute(track, (1, 1), cfg)

    if not cmd.valid:
        return {"vx": 0.0, "vy": 0.0, "vz": 0.0, "yawspeed": 0.0,
                "valid": False, "reason": cmd.reason,
                "bbox": bbox, "framing_error": framing_error}

    vx, vy, vz, yawspeed = body_to_world_velocity(
        cmd.forward_vel, cmd.vertical_vel, cmd.yaw_rate, drone_yaw)
    return {"vx": vx, "vy": vy, "vz": vz, "yawspeed": yawspeed,
            "valid": True, "reason": cmd.reason,
            "bbox": bbox, "framing_error": framing_error}


def main():
    """Live entrypoint: subscribes to PX4 SITL pose over the Phase-5 bridge,
    drives follow_tick() every control tick, and publishes a MIXED offboard
    setpoint (position-controlled altitude + velocity-controlled horizontal
    follow + yawspeed -- see build_setpoint_fields' docstring for why a
    pure-velocity setpoint never got the quad off the ground); arms +
    requests offboard mode once, via a latch, after a couple seconds of
    streaming, exactly as bench/offboard_demo.py does.

    SAFETY GATE lives HERE, not at module import -- see the module docstring
    for why, and confirm this is the only path that ever touches rclpy or the
    vehicle.
    """
    if os.environ.get("GARUDA_SIM") != "1":
        sys.exit("REFUSED: GARUDA_SIM=1 not set -- this commands arming/offboard "
                 "and may only target simulation.")

    import argparse

    import rclpy
    from px4_msgs.msg import (OffboardControlMode, TrajectorySetpoint,
                              VehicleCommand, VehicleOdometry)
    from rclpy.node import Node
    from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

    parser = argparse.ArgumentParser(description="PX4-SITL mono follow-me harness")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="seconds to stream/follow before landing (default 60)")
    parser.add_argument("--hold-alt", type=float, default=3.0,
                        help="altitude to take off to and hold, metres (default 3.0)")
    args = parser.parse_args()

    cfg = FollowConfig()

    class SitlFollowMe(Node):
        def __init__(self):
            super().__init__("sitl_followme")
            qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                             history=HistoryPolicy.KEEP_LAST, depth=5)
            self.mode_pub = self.create_publisher(OffboardControlMode, "/fmu/in/offboard_control_mode", qos)
            self.sp_pub = self.create_publisher(TrajectorySetpoint, "/fmu/in/trajectory_setpoint", qos)
            self.cmd_pub = self.create_publisher(VehicleCommand, "/fmu/in/vehicle_command", qos)
            # Pose source: /fmu/out/vehicle_odometry, NOT vehicle_local_position.
            # Live-bench finding (2026-07-30): this firmware build (47ce421)
            # publishes the local-position topic under the VERSIONED name
            # vehicle_local_position_v1, which this container's px4_msgs never
            # matches -- the node held "no pose yet" for an entire live run
            # (harmlessly -- the gate/hold logic did its job, no unsafe motion).
            # vehicle_odometry is unversioned and confirmed flowing at 98 Hz
            # with position + orientation, so pose comes from there instead.
            self.create_subscription(VehicleOdometry, "/fmu/out/vehicle_odometry",
                                     self.on_odom, qos)
            self.pose = None
            self.tick_n = 0
            self._offboard_sent = False    # latch: fire arm+offboard exactly once
            self.create_timer(1.0 / RATE_HZ, self.tick)

        def now_us(self):
            return int(self.get_clock().now().nanoseconds / 1000)

        def on_odom(self, msg):
            # Guard: q defaults to all-zero before PX4 populates it, which is
            # not a valid rotation -- treat as no-pose-yet rather than feeding
            # atan2(0,0)-derived garbage yaw into follow_tick.
            q = msg.q
            if len(q) < 4 or (q[0] == 0.0 and q[1] == 0.0 and q[2] == 0.0 and q[3] == 0.0):
                return
            self.pose = msg

        def send_cmd(self, command, p1=0.0, p2=0.0):
            c = VehicleCommand()
            c.timestamp = self.now_us()
            c.command = command
            c.param1, c.param2 = float(p1), float(p2)
            c.target_system = 1
            c.target_component = 1
            c.source_system = 1
            c.source_component = 1
            c.from_external = True
            self.cmd_pub.publish(c)

        def tick(self):
            t = self.tick_n / RATE_HZ
            self.tick_n += 1

            # offboard heartbeat -- MIXED setpoint: altitude is position-
            # controlled, horizontal + yaw are velocity-controlled (see
            # build_setpoint_fields' docstring for why), so both flags are set.
            mode = OffboardControlMode()
            mode.timestamp = self.now_us()
            mode.position = True
            mode.velocity = True
            self.mode_pub.publish(mode)

            if self.pose is None:
                x, y, z, yaw = 0.0, 0.0, 0.0, 0.0
                result = {"vx": 0.0, "vy": 0.0, "vz": 0.0, "yawspeed": 0.0,
                         "valid": False, "reason": "no pose yet",
                         "bbox": None, "framing_error": None}
            else:
                x, y, z = self.pose.position[0], self.pose.position[1], self.pose.position[2]
                yaw = quaternion_to_yaw(self.pose.q)
                result = follow_tick(x, y, z, yaw, TARGET_WORLD, cfg)

            position, velocity, yawspeed = build_setpoint_fields(result, args.hold_alt)

            sp = TrajectorySetpoint()
            sp.timestamp = self.now_us()
            sp.position = position               # [NaN, NaN, -hold_alt] -- altitude hold/climb
            sp.velocity = velocity               # [vx, vy, NaN] -- horizontal follow, vz owned by position
            sp.acceleration = [float("nan")] * 3
            sp.jerk = [float("nan")] * 3
            sp.yaw = float("nan")
            sp.yawspeed = yawspeed
            self.sp_pub.publish(sp)

            # Latch, not exact-tick equality: fires once, the first tick at or
            # past the stream-warmup window, robust to a skipped tick. The
            # mixed setpoint above (incl. the position-z takeoff target) has
            # already been streaming since tick 0, so the takeoff target is
            # present in the stream before offboard engages, as PX4 requires.
            if not self._offboard_sent and self.tick_n >= int(STREAM_BEFORE_MODE_S * RATE_HZ):
                self.send_cmd(VehicleCommand.VEHICLE_CMD_DO_SET_MODE, 1, 6)        # Offboard
                self.send_cmd(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, 1)  # arm (SIM)
                self._offboard_sent = True
                self.get_logger().info(
                    f"offboard + arm commanded (GARUDA_SIM), hold_alt={args.hold_alt:.1f}m")

            if self.tick_n % int(LOG_EVERY_S * RATE_HZ) == 0:
                fe = result["framing_error"]
                fe_s = f"{fe:.3f}" if fe is not None else "n/a"
                self.get_logger().info(
                    f"t={t:5.1f}s pos N={x:6.2f} E={y:6.2f} alt={-z:5.2f}m yaw={yaw:+.2f} "
                    f"valid={result['valid']} reason={result['reason']} framing_err={fe_s} "
                    f"cmd vx={velocity[0]:+.2f} vy={velocity[1]:+.2f} "
                    f"alt_target={-position[2]:5.2f}m yawspeed={yawspeed:+.2f}")

            if t > args.duration:
                self.send_cmd(VehicleCommand.VEHICLE_CMD_NAV_LAND)
                self.get_logger().info("landing commanded -- demo done")
                raise SystemExit

    rclpy.init()
    node = SitlFollowMe()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
