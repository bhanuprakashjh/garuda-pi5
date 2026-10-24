import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # bytetrack (sibling)

import pytest
from bytetrack import Track
from bytetrack import ByteTracker


@pytest.fixture(autouse=True)
def _reset_track_ids():
    Track._next = 1


def _step(tr, boxes, conf=0.9):
    return tr.update([(b, 0, conf) for b in boxes])


def test_single_object_keeps_id():
    tr = ByteTracker(min_hits=1)
    ids = []
    for x in range(0, 50, 10):           # object drifting right
        t = _step(tr, [[x, 0, 20, 20]])
        ids.append(t[0].track_id)
    assert len(set(ids)) == 1            # same id throughout


def test_two_objects_distinct_ids():
    tr = ByteTracker(min_hits=1)
    _step(tr, [[0, 0, 20, 20], [100, 0, 20, 20]])
    t = _step(tr, [[10, 0, 20, 20], [110, 0, 20, 20]])
    assert len({x.track_id for x in t}) == 2


def test_low_score_second_stage_recovers():
    tr = ByteTracker(min_hits=1, high_thr=0.5, low_thr=0.1)
    tr.update([([0, 0, 20, 20], 0, 0.9)])
    # next frame the same object is only weakly detected -> low-score stage keeps its id
    t = tr.update([([5, 0, 20, 20], 0, 0.2)])
    assert len(t) == 1 and t[0].track_id == 1


def test_track_dies_after_max_lost():
    tr = ByteTracker(min_hits=1, max_lost=2)
    tr.update([([0, 0, 20, 20], 0, 0.9)])
    for _ in range(4):
        out = tr.update([])              # no detections
    assert out == []                     # removed after max_lost


def test_velocity_is_steady_displacement():
    tr = ByteTracker(min_hits=1)
    vels = []
    for x in range(0, 50, 10):           # object moving +10 px/frame in x, 5 frames
        t = _step(tr, [[x, 0, 20, 20]])
        vels.append(t[0].vel)
    # velocity must reflect the steady +10 px/frame displacement since the
    # last real update, not oscillate toward 0 because predict() already
    # advanced the bbox before update() computed the delta. Checking only
    # the final frame is not sufficient: with the old (buggy) baseline the
    # oscillation is [0,0],[10,0],[0,0],[10,0],... which coincidentally
    # matches [10.0, 0.0] again after an even number of updates -- so every
    # post-birth frame is checked here to actually catch the regression.
    assert vels[0] == [0.0, 0.0]                 # birth: no prior update yet
    assert all(v == [10.0, 0.0] for v in vels[1:])  # steady from 2nd update on
