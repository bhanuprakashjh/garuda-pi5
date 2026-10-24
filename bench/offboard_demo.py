#!/usr/bin/env python3
"""Offboard flight demo: the Pi commands the (simulated) vehicle.

SAFETY GATE (CLAUDE.md §2): this script touches arming and offboard setpoints,
so it refuses to run unless GARUDA_SIM=1 is set. It must only ever be pointed
at a SITL/simulated endpoint.

Pattern: takeoff to 5 m, then a wide circular circuit (radius R) with
sinusoidal altitude variation, yaw tracking the direction of travel, then land.
Watch it in QGroundControl.
"""
import math
import os
import sys

if os.environ.get("GARUDA_SIM") != "1":
    sys.exit("REFUSED: GARUDA_SIM=1 not set — this commands arming/offboard "
             "and may only target simulation.")

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from px4_msgs.msg import OffboardControlMode, TrajectorySetpoint, VehicleCommand

RATE_HZ = 20.0
R = 500.0         # circuit radius, m (1 km diameter)
N_PTS = 24        # waypoints per lap (~131 m legs)
LEG_S = 7.0       # ~131 m at ~19 m/s
ALT_MIN, ALT_MAX = 20.0, 120.0

# Schedule of (hold_seconds, x, y, z, yaw) — NED frame.
SCHEDULE = [(10.0, 0.0, 0.0, -ALT_MIN, math.pi / 2)]  # takeoff/hover point
for i in range(1, N_PTS + 1):
    a = 2 * math.pi * i / N_PTS
    x = R - R * math.cos(a)
    y = R * math.sin(a)
    alt = ALT_MIN + (ALT_MAX - ALT_MIN) * 0.5 * (1 - math.cos(2 * a))
    yaw = math.atan2(math.cos(a), math.sin(a))
    SCHEDULE.append((LEG_S, x, y, -alt, yaw))
SCHEDULE.append((20.0, 0.0, 0.0, -ALT_MIN, math.pi / 2))  # return before landing


class OffboardDemo(Node):
    def __init__(self):
        super().__init__("offboard_demo")
        qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                         history=HistoryPolicy.KEEP_LAST, depth=5)
        self.mode_pub = self.create_publisher(OffboardControlMode, "/fmu/in/offboard_control_mode", qos)
        self.sp_pub = self.create_publisher(TrajectorySetpoint, "/fmu/in/trajectory_setpoint", qos)
        self.cmd_pub = self.create_publisher(VehicleCommand, "/fmu/in/vehicle_command", qos)
        self.tick_n = 0
        self.last_idx = -1
        self.total_s = 2.0 + sum(s[0] for s in SCHEDULE)
        self.timer = self.create_timer(1.0 / RATE_HZ, self.tick)

    def now_us(self):
        return int(self.get_clock().now().nanoseconds / 1000)

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

    def current_wp(self, t):
        acc = 2.0
        for i, wp in enumerate(SCHEDULE):
            acc += wp[0]
            if t < acc:
                return i, wp
        return len(SCHEDULE) - 1, SCHEDULE[-1]

    def tick(self):
        t = self.tick_n / RATE_HZ
        self.tick_n += 1

        mode = OffboardControlMode()
        mode.timestamp = self.now_us()
        mode.position = True
        self.mode_pub.publish(mode)

        idx, (_, x, y, z, yaw) = self.current_wp(t)
        sp = TrajectorySetpoint()
        sp.timestamp = self.now_us()
        sp.position = [x, y, z]
        sp.velocity = [float("nan")] * 3
        sp.acceleration = [float("nan")] * 3
        sp.jerk = [float("nan")] * 3
        sp.yaw = yaw
        sp.yawspeed = float("nan")
        self.sp_pub.publish(sp)

        if self.tick_n == int(2.0 * RATE_HZ):
            self.send_cmd(VehicleCommand.VEHICLE_CMD_DO_SET_MODE, 1, 6)  # Offboard
            self.send_cmd(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, 1)  # arm (SIM)
            self.get_logger().info("offboard + arm commanded (GARUDA_SIM)")
        if idx != self.last_idx:
            self.last_idx = idx
            self.get_logger().info(
                f"t={t:5.1f}s wp {idx}/{len(SCHEDULE)-1}: N={x:6.1f} E={y:6.1f} alt={-z:4.1f}m")
        if t > self.total_s:
            self.send_cmd(VehicleCommand.VEHICLE_CMD_NAV_LAND)
            self.get_logger().info("landing commanded — demo done")
            self.timer.cancel()
            raise SystemExit


def main():
    rclpy.init()
    node = OffboardDemo()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
