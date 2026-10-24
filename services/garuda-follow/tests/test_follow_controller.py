import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from garuda_interfaces import Track
from follow_controller import compute, FollowConfig

CFG = FollowConfig()

def target(cx, h, conf=0.9, tsu=0):
    # bbox centered horizontally at cx, vertically at 0.5, height h
    return Track(id=1, class_id=0, label="person",
                 bbox=(cx - 0.1, 0.5 - h / 2, 0.2, h), confidence=conf, time_since_update=tsu)

def test_centered_at_desired_size_is_near_zero_and_valid():
    c = compute(target(0.5, CFG.desired_height_frac), (1, 1), CFG)
    assert c.valid
    assert abs(c.yaw_rate) < 1e-6 and abs(c.forward_vel) < 1e-6

def test_target_left_yaws_left():
    c = compute(target(0.2, CFG.desired_height_frac), (1, 1), CFG)
    assert c.valid and c.yaw_rate < 0        # negative = turn left toward target

def test_target_right_yaws_right():
    c = compute(target(0.8, CFG.desired_height_frac), (1, 1), CFG)
    assert c.valid and c.yaw_rate > 0

def test_small_bbox_moves_forward_large_moves_back():
    small = compute(target(0.5, CFG.desired_height_frac * 0.5), (1, 1), CFG)
    large = compute(target(0.5, CFG.desired_height_frac * 1.5), (1, 1), CFG)
    assert small.forward_vel > 0 and large.forward_vel < 0

def test_output_is_clamped():
    c = compute(target(0.0, CFG.min_height_frac + 0.001), (1, 1), CFG)  # far off-center, far
    assert abs(c.yaw_rate) <= CFG.max_yaw_rate + 1e-9
    assert abs(c.forward_vel) <= CFG.max_forward_vel + 1e-9

def test_none_target_holds():
    c = compute(None, (1, 1), CFG)
    assert not c.valid and c.reason and c.forward_vel == 0.0 and c.yaw_rate == 0.0

def test_low_confidence_holds():
    c = compute(target(0.5, CFG.desired_height_frac, conf=CFG.min_confidence - 0.01), (1, 1), CFG)
    assert not c.valid and "confidence" in c.reason

def test_stale_target_holds():
    c = compute(target(0.5, CFG.desired_height_frac, tsu=CFG.max_stale_frames + 1), (1, 1), CFG)
    assert not c.valid and "stale" in c.reason

def test_implausible_size_holds():
    big = compute(target(0.5, CFG.max_height_frac + 0.05), (1, 1), CFG)
    tiny = compute(target(0.5, CFG.min_height_frac - 0.001), (1, 1), CFG)
    assert not big.valid and not tiny.valid

def target_vert(cy, h=CFG.desired_height_frac, conf=0.9, tsu=0):
    # bbox centered horizontally at 0.5, vertical center placed independently at cy
    return Track(id=1, class_id=0, label="person",
                 bbox=(0.4, cy - h / 2, 0.2, h), confidence=conf, time_since_update=tsu)

def test_target_below_center_descends():
    c = compute(target_vert(0.75), (1, 1), CFG)
    assert c.valid and c.vertical_vel < 0        # below-center (image y down) -> descend

def test_target_above_center_ascends():
    c = compute(target_vert(0.25), (1, 1), CFG)
    assert c.valid and c.vertical_vel > 0        # above-center -> ascend

def test_vertical_vel_clamped():
    c = compute(target_vert(5.0), (1, 1), CFG)   # far below center, would exceed the cap
    assert abs(c.vertical_vel) <= CFG.max_vertical_vel + 1e-9
