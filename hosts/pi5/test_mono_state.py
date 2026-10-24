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
