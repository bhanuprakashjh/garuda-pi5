#!/usr/bin/env python3
"""P5 covariance-failsafe demo: sabotage the vision feed mid-flight.

SAFETY GATE (CLAUDE.md §2): refuses to run unless GARUDA_SIM=1.

Requires SITL running VISION-PRIMARY (EKF2_GPS_CTRL=0, EKF2_EV_CTRL=3,
EKF2_HGT_REF=0). The node acts as the vehicle's only position source:
  - pre-flight: publishes origin (true: vehicle is on the pad)
  - in flight: echoes EKF odometry back as EV (self-consistent healthy feed)
  - at T_SABOTAGE: scenario "kill"   -> stops publishing entirely
                   scenario "freeze" -> repeats last pose, covariance unchanged
Offboard setpoints keep streaming throughout — any failsafe is about the
vision, not the command link.

Usage: python3 failsafe_demo.py kill|freeze
"""
import os
import sys

if os.environ.get("GARUDA_SIM") != "1":
    sys.exit("REFUSED: GARUDA_SIM=1 not set — simulation only.")

SCENARIO = sys.argv[1] if len(sys.argv) > 1 else "kill"
assert SCENARIO in ("kill", "freeze"), "scenario must be 'kill' or 'freeze'"

import math
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from px4_msgs.msg import (OffboardControlMode, TrajectorySetpoint,
                          VehicleCommand, VehicleOdometry, VehicleStatus)

RATE_HZ = 20.0
EV_RATE_DIV = 1          # EV published every tick (20 Hz)
T_MODE = 4.0             # offboard + arm after EKF has fused EV a while
T_SABOTAGE = 25.0        # sabotage mid-circuit
T_END = 60.0             # observe the aftermath, then exit
R, N_PTS, LEG_S = 100.0, 8, 4.0
ALT = 15.0

SCHEDULE = [(8.0, 0.0, 0.0, -ALT, math.pi / 2)]
for i in range(1, N_PTS + 1):
    a = 2 * math.pi * i / N_PTS
    SCHEDULE.append((LEG_S, R - R * math.cos(a), R * math.sin(a), -ALT,
                     math.atan2(math.cos(a), math.sin(a))))


class FailsafeDemo(Node):
    def __init__(self):
        super().__init__("failsafe_demo")
        qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                         history=HistoryPolicy.KEEP_LAST, depth=5)
        self.mode_pub = self.create_publisher(OffboardControlMode, "/fmu/in/offboard_control_mode", qos)
        self.sp_pub = self.create_publisher(TrajectorySetpoint, "/fmu/in/trajectory_setpoint", qos)
        self.cmd_pub = self.create_publisher(VehicleCommand, "/fmu/in/vehicle_command", qos)
        self.ev_pub = self.create_publisher(VehicleOdometry, "/fmu/in/vehicle_visual_odometry", qos)
        self.create_subscription(VehicleOdometry, "/fmu/out/vehicle_odometry", self.on_odom, qos)
        self.create_subscription(VehicleStatus, "/fmu/out/vehicle_status_v4", self.on_status, qos)
        self.last_odom = None
        self.frozen_pose = None
        self.nav_state = None
        self.sabotaged = False
        self.tick_n = 0
        self.create_timer(1.0 / RATE_HZ, self.tick)

    def now_us(self):
        return int(self.get_clock().now().nanoseconds / 1000)

    def on_odom(self, msg):
        self.last_odom = msg

    def on_status(self, msg):
        if msg.nav_state != self.nav_state:
            t = self.tick_n / RATE_HZ
            self.get_logger().warning(
                f"t={t:5.1f}s NAV_STATE CHANGE: {self.nav_state} -> {msg.nav_state}"
                f"{'  <-- FAILSAFE REACTION' if self.sabotaged else ''}")
            self.nav_state = msg.nav_state

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

    def publish_ev(self):
        ev = VehicleOdometry()
        now = self.now_us()
        if self.sabotaged and SCENARIO == "freeze" and self.frozen_pose is not None:
            src = self.frozen_pose      # stale pose, fresh timestamps, same covariance
            ev.position = list(src.position)
            ev.q = list(src.q)
        elif self.last_odom is not None and not any(
                math.isnan(v) for v in self.last_odom.position):
            ev.position = list(self.last_odom.position)
            ev.q = list(self.last_odom.q)
        else:
            ev.position = [0.0, 0.0, 0.0]   # pre-init bootstrap: true on the pad
            ev.q = [1.0, 0.0, 0.0, 0.0]
        ev.timestamp = now
        ev.timestamp_sample = now
        ev.pose_frame = VehicleOdometry.POSE_FRAME_NED
        ev.velocity_frame = VehicleOdometry.VELOCITY_FRAME_NED
        ev.velocity = [float("nan")] * 3
        ev.angular_velocity = [float("nan")] * 3
        ev.position_variance = [0.01, 0.01, 0.01]
        ev.orientation_variance = [0.0025, 0.0025, 0.0025]
        ev.velocity_variance = [float("nan")] * 3
        ev.quality = 100
        self.ev_pub.publish(ev)

    def current_wp(self, t):
        acc = T_MODE
        for wp in SCHEDULE:
            acc += wp[0]
            if t < acc:
                return wp
        return SCHEDULE[-1]

    def tick(self):
        t = self.tick_n / RATE_HZ
        self.tick_n += 1

        # offboard heartbeat + setpoint: NEVER stops (isolates vision failure)
        mode = OffboardControlMode()
        mode.timestamp = self.now_us()
        mode.position = True
        self.mode_pub.publish(mode)

        _, x, y, z, yaw = self.current_wp(t)
        sp = TrajectorySetpoint()
        sp.timestamp = self.now_us()
        sp.position = [x, y, z]
        sp.velocity = [float("nan")] * 3
        sp.acceleration = [float("nan")] * 3
        sp.jerk = [float("nan")] * 3
        sp.yaw = yaw
        sp.yawspeed = float("nan")
        self.sp_pub.publish(sp)

        # the vision feed — healthy, then sabotaged
        if not self.sabotaged and t >= T_SABOTAGE:
            self.sabotaged = True
            self.frozen_pose = self.last_odom
            self.get_logger().error(
                f"t={t:5.1f}s *** SABOTAGE: vision feed {SCENARIO.upper()} ***")
        if not (self.sabotaged and SCENARIO == "kill"):
            if self.tick_n % EV_RATE_DIV == 0:
                self.publish_ev()

        if self.tick_n == int(T_MODE * RATE_HZ):
            self.send_cmd(VehicleCommand.VEHICLE_CMD_DO_SET_MODE, 1, 6)
            self.send_cmd(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, 1)
            self.get_logger().info("offboard + arm commanded (GARUDA_SIM)")

        if self.tick_n % int(5 * RATE_HZ) == 0 and self.last_odom is not None:
            p = self.last_odom.position
            self.get_logger().info(
                f"t={t:5.1f}s est N={p[0]:6.1f} E={p[1]:6.1f} alt={-p[2]:5.1f}m "
                f"nav_state={self.nav_state} sabotaged={self.sabotaged}")

        if t > T_END:
            self.get_logger().info("demo window over — exiting (failsafe owns the vehicle)")
            raise SystemExit


def main():
    rclpy.init()
    rclpy.spin(FailsafeDemo())


if __name__ == "__main__":
    main()
