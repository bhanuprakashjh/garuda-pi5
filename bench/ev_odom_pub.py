#!/usr/bin/env python3
"""Minimal external-vision odometry publisher for P5 bridge testing.

Publishes a stationary pose at the origin (NED) with plausible covariance to
/fmu/in/vehicle_visual_odometry at 30 Hz — just enough for EKF2 to accept and
fuse external vision. This is bench tooling, not a Garuda service: the real
feed will come from garuda-vio via the bridge service.

Run inside the garuda/ros-jazzy container (px4_ws sourced):
  python3 ev_odom_pub.py
"""
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from px4_msgs.msg import VehicleOdometry


class EvOdomPub(Node):
    def __init__(self):
        super().__init__("ev_odom_pub")
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        self.pub = self.create_publisher(
            VehicleOdometry, "/fmu/in/vehicle_visual_odometry", qos
        )
        self.timer = self.create_timer(1.0 / 30.0, self.tick)
        self.n = 0

    def tick(self):
        msg = VehicleOdometry()
        now_us = int(self.get_clock().now().nanoseconds / 1000)
        msg.timestamp = now_us
        msg.timestamp_sample = now_us
        msg.pose_frame = VehicleOdometry.POSE_FRAME_NED
        msg.position = [0.0, 0.0, 0.0]
        msg.q = [1.0, 0.0, 0.0, 0.0]
        msg.velocity_frame = VehicleOdometry.VELOCITY_FRAME_NED
        msg.velocity = [float("nan")] * 3          # not providing velocity
        msg.angular_velocity = [float("nan")] * 3  # not providing rates
        msg.position_variance = [0.01, 0.01, 0.01]
        msg.orientation_variance = [0.0025, 0.0025, 0.0025]
        msg.velocity_variance = [float("nan")] * 3
        msg.quality = 100
        self.pub.publish(msg)
        self.n += 1
        if self.n % 150 == 0:
            self.get_logger().info(f"published {self.n} EV odometry msgs")


def main():
    rclpy.init()
    node = EvOdomPub()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
