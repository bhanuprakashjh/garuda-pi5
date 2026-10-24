"""Dev-PC tests for sitl_followme.py's PURE core -- no ROS.

Design note (mirrors the comment at the top of sitl_followme.py): the
GARUDA_SIM safety gate lives inside `main()`, not at module import time, so
this file can `import sitl_followme` / `from sitl_followme import
follow_tick` on a machine with no rclpy/px4_msgs installed and with
GARUDA_SIM unset -- exactly this dev-PC/CI environment. We deliberately do
NOT set GARUDA_SIM=1 anywhere in this file: that's the point of the test --
importing (and calling follow_tick) must work with the flag absent, while
main() must still refuse without it (see
test_main_refuses_without_garuda_sim_before_importing_rclpy below).

Run: `pytest services/ -v` from the repo root, fully green, no rclpy needed.
"""
import math
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from follow_controller import FollowConfig
from sitl_followme import (body_to_world_velocity, build_setpoint_fields,
                           follow_tick, quaternion_to_yaw)

CFG = FollowConfig()


# ---------------------------------------------------------------------------
# follow_tick -- end to end (project -> compute -> world-frame mapping)
# ---------------------------------------------------------------------------

def test_target_ahead_at_good_range_is_valid_with_small_commands():
    # drone at origin facing +x, target dead ahead at a range that puts the
    # bbox height very close to the desired framing (h=0.48 vs desired 0.50)
    # -> valid, and both yaw and forward commands should be small (near the
    # setpoint, not zero -- this is NOT the same as the on-target/zero case).
    result = follow_tick(0.0, 0.0, 0.0, 0.0, (1.25, 0.0), CFG)
    assert result["valid"] is True
    assert abs(result["yawspeed"]) < 0.05
    assert abs(result["vx"]) < 0.2
    assert abs(result["vy"]) < 1e-9


def test_target_off_to_the_side_yaws_with_correct_sign():
    # target to positive y (right of a drone facing +x) -> the controller
    # must yaw right (positive yaw_rate/yawspeed), per follow_controller's
    # own sign convention (see test_follow_controller.test_target_right_yaws_right).
    right = follow_tick(0.0, 0.0, 0.0, 0.0, (5.0, 3.0), CFG)
    assert right["valid"] is True
    assert right["yawspeed"] > 0.0

    left = follow_tick(0.0, 0.0, 0.0, 0.0, (5.0, -3.0), CFG)
    assert left["valid"] is True
    assert left["yawspeed"] < 0.0


def test_target_behind_projects_to_none_and_holds():
    # target directly behind the drone -> projection.project returns None
    # -> follow_tick must return a full HOLD: all-zero velocity, invalid,
    # with a reason (never invent motion toward an unseen target).
    result = follow_tick(0.0, 0.0, 0.0, 0.0, (-5.0, 0.0), CFG)
    assert result["valid"] is False
    assert result["vx"] == 0.0
    assert result["vy"] == 0.0
    assert result["vz"] == 0.0
    assert result["yawspeed"] == 0.0
    assert result["reason"]
    assert result["bbox"] is None


def test_world_frame_mapping_yaw_zero_maps_forward_to_vx():
    # yaw=0: target far dead-ahead needs forward motion -> vx>0, vy~=0.
    result = follow_tick(0.0, 0.0, 0.0, 0.0, (10.0, 0.0), CFG)
    assert result["valid"] is True
    assert result["vx"] > 0.0
    assert abs(result["vy"]) < 1e-9


def test_world_frame_mapping_yaw_pi_over_2_maps_forward_to_vy():
    # yaw=pi/2 (drone facing +y): target far dead-ahead of the drone's own
    # heading -> body-forward motion must land entirely on vy in world frame,
    # not vx (proves the cos/sin rotation is applied, not skipped).
    result = follow_tick(0.0, 0.0, 0.0, math.pi / 2, (0.0, 10.0), CFG)
    assert result["valid"] is True
    assert result["vy"] > 0.0
    assert abs(result["vx"]) < 1e-9


# ---------------------------------------------------------------------------
# body_to_world_velocity -- direct unit tests of the mapping (incl. vz sign)
# ---------------------------------------------------------------------------
# projection.py's vfov is unused (documented in projection.py), so the bbox
# vertical center is always frame-center and compute()'s vertical_vel is
# always 0 through the full follow_tick pipeline. To prove the vz sign
# convention (independent of that projection limitation) we exercise the
# mapping function directly with a synthetic vertical_vel.

def test_body_to_world_velocity_vz_sign_up_is_negative_z():
    vx, vy, vz, yawspeed = body_to_world_velocity(
        forward_vel=0.0, vertical_vel=1.0, yaw_rate=0.0, yaw=0.0)
    assert vz < 0.0
    assert vz == -1.0


def test_body_to_world_velocity_yaw_zero():
    vx, vy, vz, yawspeed = body_to_world_velocity(
        forward_vel=2.0, vertical_vel=0.0, yaw_rate=0.3, yaw=0.0)
    assert vx > 0.0
    assert abs(vy) < 1e-9
    assert yawspeed == 0.3


def test_body_to_world_velocity_yaw_pi_over_2():
    vx, vy, vz, yawspeed = body_to_world_velocity(
        forward_vel=2.0, vertical_vel=0.0, yaw_rate=-0.3, yaw=math.pi / 2)
    assert abs(vx) < 1e-9
    assert vy > 0.0
    assert yawspeed == -0.3          # yaw_rate is frame-invariant, unrotated


# ---------------------------------------------------------------------------
# build_setpoint_fields -- pure helper added when the live bench found a
# pure-velocity setpoint (vz always 0, follow_tick's vz is a 2D no-op) never
# commanded a climb, so the quad just sat armed on the ground. Fix: MIXED
# per-axis setpoint -- position[2] holds/climbs to altitude, velocity[0:2]
# carries the horizontal follow, velocity[2] stays NaN (owned by position).
# ---------------------------------------------------------------------------

def test_build_setpoint_fields_valid_result_holds_altitude_and_follows_horizontally():
    result = {"vx": 1.25, "vy": 0.4, "vz": 0.0, "yawspeed": 0.8, "valid": True, "reason": "tracking"}
    position, velocity, yawspeed = build_setpoint_fields(result, hold_alt=3.0)
    assert position[0] != position[0] and position[1] != position[1]   # NaN != NaN
    assert position[2] == -3.0                                          # NED down
    assert velocity[0] == 1.25 and velocity[1] == 0.4
    assert velocity[2] != velocity[2]                                   # NaN -- owned by position
    assert yawspeed == 0.8


def test_build_setpoint_fields_hold_result_hovers_at_altitude_not_zero_everything():
    # invalid/HOLD result (no pose yet / target lost) must still command the
    # altitude hold -- "lost target" means hover in place while airborne, not
    # "stop commanding altitude too" (which would let the quad fall/drift).
    result = {"vx": 0.0, "vy": 0.0, "vz": 0.0, "yawspeed": 0.0, "valid": False, "reason": "no target"}
    position, velocity, yawspeed = build_setpoint_fields(result, hold_alt=3.0)
    assert position[2] == -3.0            # altitude hold still commanded
    assert velocity[0] == 0.0 and velocity[1] == 0.0
    assert velocity[2] != velocity[2]      # NaN
    assert yawspeed == 0.0


def test_build_setpoint_fields_different_hold_alt_changes_only_position_z():
    result = {"vx": 0.5, "vy": 0.0, "vz": 0.0, "yawspeed": 0.0, "valid": True, "reason": "tracking"}
    position, velocity, yawspeed = build_setpoint_fields(result, hold_alt=5.5)
    assert position[2] == -5.5
    assert velocity[0] == 0.5


# ---------------------------------------------------------------------------
# quaternion_to_yaw -- pure helper added when the live bench found this
# firmware publishes vehicle_local_position under the versioned topic name
# vehicle_local_position_v1 (not matched by this container's px4_msgs), so
# pose now comes from /fmu/out/vehicle_odometry (position + quaternion)
# instead -- yaw has to be derived from the quaternion.
# ---------------------------------------------------------------------------

def test_quaternion_to_yaw_identity_is_zero():
    assert abs(quaternion_to_yaw([1.0, 0.0, 0.0, 0.0]) - 0.0) < 1e-9


def test_quaternion_to_yaw_90deg_yaw_quaternion_is_pi_over_2():
    # pure yaw rotation of +90deg about NED z: q = [cos(t/2), 0, 0, sin(t/2)]
    t = math.pi / 2
    q = [math.cos(t / 2), 0.0, 0.0, math.sin(t / 2)]
    assert abs(quaternion_to_yaw(q) - math.pi / 2) < 1e-9


def test_quaternion_to_yaw_minus_90deg_yaw_quaternion_is_minus_pi_over_2():
    t = -math.pi / 2
    q = [math.cos(t / 2), 0.0, 0.0, math.sin(t / 2)]
    assert abs(quaternion_to_yaw(q) - (-math.pi / 2)) < 1e-9


# ---------------------------------------------------------------------------
# GARUDA_SIM gate -- confirms the gate lives in main(), before `import rclpy`
# ---------------------------------------------------------------------------

def test_main_refuses_without_garuda_sim_before_importing_rclpy():
    # rclpy is NOT installed in this dev-PC environment (by design -- follow_tick
    # must not need it). If the gate check in main() ran AFTER `import rclpy`,
    # this would raise ModuleNotFoundError instead of the clean SystemExit
    # refusal -- so this test also proves the gate is the very first thing
    # main() does.
    import sitl_followme
    os.environ.pop("GARUDA_SIM", None)
    with pytest.raises(SystemExit) as exc_info:
        sitl_followme.main()
    msg = str(exc_info.value)
    assert "REFUSED" in msg
    assert "GARUDA_SIM" in msg
