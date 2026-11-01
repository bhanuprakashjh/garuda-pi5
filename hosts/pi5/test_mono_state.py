from mono_state import build_snapshot

STATE = {"class": "person", "raw": 2.5, "t": 100.0, "count": 2,
         "tracks_full": [(3, "person", 10.0, 20.0, 30.0, 40.0, 0.9, False),
                         (7, "chair", 50.0, 60.0, 70.0, 80.0, 0.4, True)]}
EXTRAS = {"lock_id": 3, "lock_coasting": False, "est_m": 2.4, "valid": True,
          "depth_engine": "y26d", "detector_mode": "primary", "prompt": [],
          "cal_n": 0, "fit": None, "age_ms": 120, "frame_w": 1280, "frame_h": 720}

def test_snapshot_shape_and_tracks():
    s = build_snapshot(STATE, EXTRAS)
    assert s["v"] == 2 and s["age_ms"] == 120
    assert s["tracks"][0] == {"id": 3, "label": "person", "conf": 0.9,
                              "x": 10.0, "y": 20.0, "w": 30.0, "h": 40.0, "coasting": False}
    assert s["lock"] == {"id": 3, "class": "person", "coasting": False}
    assert s["distance"] == {"raw_m": 2.5, "est_m": 2.4, "valid": True}
    assert s["frame"] == {"width": 1280, "height": 720}

def test_snapshot_no_lock_no_distance():
    s = build_snapshot({**STATE, "raw": None}, {**EXTRAS, "lock_id": None,
                                                "est_m": None, "valid": False})
    assert s["lock"]["id"] is None
    assert s["distance"] == {"raw_m": None, "est_m": None, "valid": False}

def test_snapshot_tolerates_short_track_tuples():
    # pre-migration 6-tuples (no conf/coasting) must not crash: conf 0.0, coasting False
    s = build_snapshot({**STATE, "tracks_full": [(1, "person", 1.0, 2.0, 3.0, 4.0)]}, EXTRAS)
    assert s["tracks"][0]["conf"] == 0.0 and s["tracks"][0]["coasting"] is False


def test_v2_tracks_carry_kpts_and_gesture():
    kp = [[1.0, 2.0, 0.9]] * 17
    st = {**STATE, "tracks_full": [(3, "person", 10.0, 20.0, 30.0, 40.0, 0.9, False, kp)]}
    s = build_snapshot(st, {**EXTRAS, "gesture": {"name": "stop", "track_id": 3, "held_ms": 1200}})
    assert s["v"] == 2
    assert s["tracks"][0]["kpts"] == kp
    assert s["gesture"] == {"name": "stop", "track_id": 3, "held_ms": 1200}


def test_v2_kpts_absent_when_not_pose_mode():
    s = build_snapshot(STATE, EXTRAS)          # 8-tuples, no gesture extra
    assert "kpts" not in s["tracks"][0]
    assert s["gesture"] == {"name": None, "track_id": None, "held_ms": 0}


def test_attach_kpts_matches_by_iou():
    from mono_state import attach_kpts
    kp = [[5.0, 5.0, 0.9]] * 17
    class D:  # duck-typed det
        def __init__(self, x, y, w, h, kpts): self.x, self.y, self.w, self.h, self.kpts = x, y, w, h, kpts
    tf = [(1, "person", 10.0, 10.0, 100.0, 200.0, 0.9, False)]
    out = attach_kpts(tf, [D(12.0, 11.0, 98.0, 205.0, kp), D(500.0, 500.0, 50.0, 50.0, None)])
    assert out[0][8] == kp
    out2 = attach_kpts(tf, [D(500.0, 500.0, 50.0, 50.0, kp)])   # no overlap -> no kpts
    assert len(out2[0]) == 8


def test_snapshot_carries_stats_passthrough():
    st = build_snapshot(STATE, {**EXTRAS, "stats": {"fps": 24.8, "unique_total": 17, "temp_c": 61.2}})
    assert st["stats"] == {"fps": 24.8, "unique_total": 17, "temp_c": 61.2}


def test_snapshot_stats_default_empty():
    assert build_snapshot(STATE, EXTRAS)["stats"] == {}


def test_invalid_fit_is_not_replaced_with_raw_distance():
    for value in (-1.35, 0, float('nan'), float('inf'), 100):
        d = build_snapshot(STATE, {**EXTRAS, "est_m": value})["distance"]
        assert d == {"raw_m": None, "est_m": None, "valid": False}


def test_stale_range_is_not_exposed_as_raw_fallback():
    assert build_snapshot(STATE, {**EXTRAS, "valid": False})["distance"]["raw_m"] is None


def test_relative_depth_is_not_labelled_metres():
    s = build_snapshot(STATE, {**EXTRAS, "depth_engine": "da", "est_m": None})
    assert s["distance"] == {"raw_m": None, "est_m": None, "valid": False}


def test_relative_depth_can_have_large_raw_values_with_valid_calibration():
    s = build_snapshot({**STATE, "raw": 1000}, {**EXTRAS, "depth_engine": "da", "est_m": 2.5})
    assert s["distance"] == {"raw_m": None, "est_m": 2.5, "valid": True}


def test_snapshot_carries_capture_identity_and_actual_dimensions():
    s = build_snapshot({**STATE, "frame_seq": 22, "frame_source": "/dev/video0",
                        "frame_epoch": "abc:0", "frame_w": 640, "frame_h": 480}, EXTRAS)
    assert s["frame"] == {"seq": 22, "source": "/dev/video0", "epoch": "abc:0", "width": 640, "height": 480}


def test_appearance_is_attached_only_to_its_own_track():
    descriptor = [0.0] * 128
    s = build_snapshot({**STATE, "appearances": {3: descriptor}}, EXTRAS)
    assert s["tracks"][0]["appearance"] == descriptor
    assert "appearance" not in s["tracks"][1]


def test_face_and_gesture_are_attached_to_the_correct_subject():
    face = {"embedding": [0]*128, "sample": "capture-1", "age_ms": 50}
    gesture = {"name": "stop", "held_ms": 1100}
    s = build_snapshot({**STATE, "faces": {3: face}, "track_gestures": {7: gesture}}, EXTRAS)
    assert s["tracks"][0]["face"] == face
    assert "face" not in s["tracks"][1]
    assert s["tracks"][1]["gesture"] == gesture


def test_explicit_selection_never_switches_to_bystander():
    from types import SimpleNamespace
    from mono_state import select_track
    a = SimpleNamespace(track_id=1, cls="person", time_since_update=9, conf=.6)
    b = SimpleNamespace(track_id=2, cls="person", time_since_update=0, conf=.9)
    assert select_track([a, b], 1, "person", True, 5) is a
    assert select_track([b], 1, "person", True, 5) is None
    assert select_track([a, b], 1, "person", False, 5) is b
