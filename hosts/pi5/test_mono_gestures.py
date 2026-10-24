from mono_gestures import classify_pose, GestureFSM

# Synthetic 17-kpt skeleton helper: a standing person in a 200x400 px box at origin.
# Indices: 0 nose, 5/6 shoulders, 9/10 wrists (others filled but unused by the rules).
def person(nose=(100, 50), lsho=(60, 120), rsho=(140, 120),
           lwri=(40, 200), rwri=(160, 200), conf=0.9):
    k = [[100.0, 250.0, conf]] * 17
    k[0] = [*map(float, nose), conf]
    k[5] = [*map(float, lsho), conf]; k[6] = [*map(float, rsho), conf]
    k[9] = [*map(float, lwri), conf]; k[10] = [*map(float, rwri), conf]
    return k

BOX = dict(box_w=200.0, box_h=400.0)

def test_neutral_pose_is_none():
    assert classify_pose(person(), **BOX) is None

def test_stop_both_wrists_above_nose():
    k = person(lwri=(60, 20), rwri=(140, 20))
    assert classify_pose(k, **BOX) == "stop"

def test_follow_me_one_arm_straight_up():
    k = person(lwri=(62, 20))                      # left wrist above nose, near shoulder x
    assert classify_pose(k, **BOX) == "follow_me"

def test_raised_but_sideways_arm_is_not_follow_me():
    k = person(lwri=(10, 20))                      # above nose but far off shoulder x
    assert classify_pose(k, **BOX) is None

def test_point_right_horizontal_extension():
    k = person(rwri=(200, 122))                    # at shoulder height, extended right
    assert classify_pose(k, **BOX) == "point_right"

def test_point_left():
    k = person(lwri=(0, 118))
    assert classify_pose(k, **BOX) == "point_left"

def test_low_confidence_keypoints_silence_the_classifier():
    k = person(lwri=(60, 20), rwri=(140, 20), conf=0.2)
    assert classify_pose(k, **BOX) is None

def test_fsm_requires_hold_and_release():
    t = {"now": 0.0}
    fsm = GestureFSM(hold_ms=1000, release_ms=400, clock=lambda: t["now"])
    assert fsm.update("stop")["name"] is None            # onset, not held yet
    t["now"] = 0.5
    assert fsm.update("stop")["name"] is None
    t["now"] = 1.1
    g = fsm.update("stop"); assert g["name"] == "stop" and g["held_ms"] >= 1000
    t["now"] = 1.2
    assert fsm.update(None)["name"] == "stop"            # brief dropout: held (hysteresis)
    t["now"] = 1.7
    assert fsm.update(None)["name"] is None              # released after release_ms

def test_fsm_changing_gesture_restarts_hold():
    t = {"now": 0.0}
    fsm = GestureFSM(hold_ms=1000, release_ms=400, clock=lambda: t["now"])
    fsm.update("stop"); t["now"] = 0.9; fsm.update("follow_me")
    t["now"] = 1.5
    assert fsm.update("follow_me")["name"] is None       # only 0.6 s of follow_me
    t["now"] = 2.0
    assert fsm.update("follow_me")["name"] == "follow_me"

def test_5s_gap_resets_hold():
    """Gap >= release_ms resets the candidate hold timer: reappearance needs fresh hold_ms."""
    t = {"now": 0.0}
    fsm = GestureFSM(hold_ms=1000, release_ms=400, clock=lambda: t["now"])
    assert fsm.update("stop")["name"] is None  # First sighting
    t["now"] = 0.1
    assert fsm.update(None)["name"] is None    # Brief dropout
    t["now"] = 5.1
    # At t=5.1, 5s have elapsed total but only 0.1s continuous before the 5s gap.
    # The gap (5.0s) >= release_ms (0.4s), so the hold timer resets at t=5.1.
    # Hold has only been 0 ms since reappearance, so not activated yet.
    g = fsm.update("stop")
    assert g["name"] is None, f"Expected None after 5s gap, got {g}"

def test_pre_activation_flicker_does_not_restart_hold():
    """Brief dropout < release_ms does not restart the hold timer."""
    t = {"now": 0.0}
    fsm = GestureFSM(hold_ms=1000, release_ms=400, clock=lambda: t["now"])
    assert fsm.update("stop")["name"] is None
    t["now"] = 0.15
    assert fsm.update(None)["name"] is None  # Brief dropout
    t["now"] = 0.25
    assert fsm.update("stop")["name"] is None  # Reappearance, gap was only 0.1s < 0.4s
    # Hold timer should still count from t=0.0, not reset at t=0.25
    t["now"] = 1.05
    # (1.05 - 0.0) * 1000 = 1050 ms >= 1000 ms, so should activate
    g = fsm.update("stop")
    assert g["name"] == "stop", f"Expected 'stop', got {g}"
    assert g["held_ms"] >= 1000

def test_release_followed_by_reappearance_requires_fresh_hold():
    """After active gesture releases, reappearance requires a fresh hold_ms."""
    t = {"now": 0.0}
    fsm = GestureFSM(hold_ms=1000, release_ms=400, clock=lambda: t["now"])
    fsm.update("stop")
    t["now"] = 1.1
    assert fsm.update("stop")["name"] == "stop"  # Activate
    t["now"] = 1.2
    assert fsm.update(None)["name"] == "stop"    # Brief dropout, still active
    t["now"] = 1.7
    assert fsm.update(None)["name"] is None      # Released after 400ms absence
    t["now"] = 1.8
    g = fsm.update("stop")
    assert g["name"] is None, f"Expected None after release, got {g}"  # Reappeared but not held yet
    t["now"] = 2.9
    # 1.1s since reappearance at t=1.8 (buffer for floating-point precision)
    g = fsm.update("stop")
    assert g["name"] == "stop", f"Expected 'stop' after fresh hold, got {g}"
    assert g["held_ms"] >= 1000


def test_fsm_direct_gesture_switch_does_not_clobber_new_hold():
    # deferred-minor fix (final review triage): releasing "stop" must not wipe the follow_me
    # candidate already accumulating hold — direct switch costs hold_ms, not hold_ms+release_ms
    t = {"now": 0.0}
    fsm = GestureFSM(hold_ms=1000, release_ms=400, clock=lambda: t["now"])
    fsm.update("stop")
    t["now"] = 1.1
    assert fsm.update("stop")["name"] == "stop"            # stop active
    t["now"] = 1.2
    fsm.update("follow_me")                                # direct switch: new candidate @1.2
    t["now"] = 1.7
    g = fsm.update("follow_me")                            # stop releases here (0.6s > release_ms)
    assert g["name"] is None                               # follow_me not yet held long enough
    t["now"] = 2.3
    assert fsm.update("follow_me")["name"] == "follow_me"  # 1.1s hold from 1.2 — NOT restarted


# Per-rule confidence gating (owner-ruled 2026-08-10, from the 4 m bench log): the idle
# wrist (c~0.06) and nose (c~0.2) used to silence every gesture via the all-five gate.

def test_point_fires_with_dead_off_arm_and_weak_nose():
    k = person(rwri=(200, 122))
    k[0] = [100.0, 50.0, 0.2]       # nose unconfident — POINT does not use it
    k[9] = [40.0, 200.0, 0.06]      # idle left wrist unconfident
    assert classify_pose(k, **BOX) == "point_right"


def test_follow_me_fires_with_dead_off_arm():
    k = person(lwri=(62, 20))
    k[10] = [160.0, 200.0, 0.1]     # idle right wrist unconfident -> reads as not raised
    assert classify_pose(k, **BOX) == "follow_me"


def test_stop_with_one_unconfident_wrist_degrades_to_confident_arm():
    # documented edge: a both-arms STOP where one wrist drops below the floor reads as the
    # confident arm alone (vertical raise -> follow_me); prefer a defined outcome over silence
    k = person(lwri=(60, 20), rwri=(140, 20))
    k[10] = [140.0, 20.0, 0.1]
    assert classify_pose(k, **BOX) == "follow_me"


def test_unconfident_gesturing_wrist_still_silences():
    k = person(rwri=(200, 122))
    k[10] = [200.0, 122.0, 0.1]     # the POINTING wrist itself is unconfident -> no gesture
    assert classify_pose(k, **BOX) is None
