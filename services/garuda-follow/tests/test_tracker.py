import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tracker import Tracker, iou

def det(x, y, w, h, label="person", conf=0.9, cid=0):
    return {"class_id": cid, "label": label, "confidence": conf, "x": x, "y": y, "w": w, "h": h}

def test_iou_identical_is_one_and_disjoint_is_zero():
    assert abs(iou((0.1, 0.1, 0.2, 0.2), (0.1, 0.1, 0.2, 0.2)) - 1.0) < 1e-6
    assert abs(iou((0.0, 0.0, 0.1, 0.1), (0.5, 0.5, 0.1, 0.1)) - 0.0) < 1e-6

def test_same_object_keeps_id_across_frames():
    tr = Tracker()
    a = tr.update([det(0.40, 0.40, 0.20, 0.20)])
    b = tr.update([det(0.42, 0.41, 0.20, 0.20)])  # small motion, high IoU
    assert len(a) == 1 and len(b) == 1
    assert a[0].id == b[0].id
    assert b[0].hits == 2 and b[0].time_since_update == 0

def test_distinct_objects_get_distinct_ids():
    tr = Tracker()
    out = tr.update([det(0.1, 0.1, 0.1, 0.1), det(0.8, 0.8, 0.1, 0.1)])
    assert len({t.id for t in out}) == 2

def test_track_dropped_after_max_age():
    tr = Tracker(max_age=2)
    first = tr.update([det(0.4, 0.4, 0.2, 0.2)])[0]
    for _ in range(3):
        out = tr.update([])            # target gone
    assert all(t.id != first.id for t in out) and out == []

def test_reappearing_after_drop_gets_new_id():
    tr = Tracker(max_age=1)
    a = tr.update([det(0.4, 0.4, 0.2, 0.2)])[0]
    tr.update([]); tr.update([])       # dropped
    c = tr.update([det(0.4, 0.4, 0.2, 0.2)])[0]
    assert c.id != a.id                # honest: no re-ID this increment

def test_class_id_gating_prevents_cross_class_matching():
    """Heavily overlapping boxes with different class_id must spawn separate tracks."""
    tr = Tracker()
    dets = [
        det(0.4, 0.4, 0.2, 0.2, label="person", cid=0),
        det(0.41, 0.4, 0.2, 0.2, label="car", cid=2)
    ]
    out1 = tr.update(dets)
    assert len(out1) == 2
    person_track = next(t for t in out1 if t.class_id == 0)
    car_track = next(t for t in out1 if t.class_id == 2)
    assert person_track.id != car_track.id

    # Second frame: same detections should match to same IDs per class, not cross-match
    out2 = tr.update(dets)
    assert len(out2) == 2
    person_track_2 = next(t for t in out2 if t.class_id == 0)
    car_track_2 = next(t for t in out2 if t.class_id == 2)
    assert person_track_2.id == person_track.id
    assert car_track_2.id == car_track.id

def test_track_boundary_at_max_age_exact():
    """Track should survive exactly max_age misses, not max_age+1."""
    # Test survival at max_age: track should still exist after 2 misses (tsu==2 <= max_age==2)
    tr1 = Tracker(max_age=2)
    track1 = tr1.update([det(0.4, 0.4, 0.2, 0.2)])[0]
    id1 = track1.id
    # 2 misses: tsu becomes 2
    tr1.update([])
    tr1.update([])
    # Re-detect: should match same ID (track still exists)
    out_after_2_misses = tr1.update([det(0.4, 0.4, 0.2, 0.2)])
    assert any(t.id == id1 for t in out_after_2_misses), "Track should survive exactly max_age misses"

    # Test dropout at max_age+1: track should be gone after 3 misses (tsu==3 > max_age==2)
    tr2 = Tracker(max_age=2)
    track2 = tr2.update([det(0.4, 0.4, 0.2, 0.2)])[0]
    id2 = track2.id
    # 3 misses: tsu becomes 3
    tr2.update([])
    tr2.update([])
    tr2.update([])
    # Re-detect: should get new ID (track was dropped)
    out_after_3_misses = tr2.update([det(0.4, 0.4, 0.2, 0.2)])
    assert all(t.id != id2 for t in out_after_3_misses), "Track should be dropped after max_age+1 misses"
