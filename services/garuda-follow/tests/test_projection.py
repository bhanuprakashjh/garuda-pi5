import sys, pathlib, math
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from projection import project

def test_target_straight_ahead_centers():
    bbox = project((5.0, 0.0), (0.0, 0.0), 0.0)
    assert bbox is not None
    cx = bbox[0] + bbox[2] / 2
    assert abs(cx - 0.5) < 1e-6

def test_target_behind_is_none():
    assert project((-5.0, 0.0), (0.0, 0.0), 0.0) is None

def test_closer_target_has_taller_bbox():
    near = project((3.0, 0.0), (0.0, 0.0), 0.0)
    far = project((9.0, 0.0), (0.0, 0.0), 0.0)
    assert near[3] > far[3]
