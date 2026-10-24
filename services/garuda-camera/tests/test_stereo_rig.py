import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from stereo_rig import StereoRig  # needs cv2 (runs on-device); pure logic below


def _rig(reference_is_cam2):
    # bypass __init__ (no YAML / hardware) to unit-test the pure ordering logic
    r = StereoRig.__new__(StereoRig)
    r.reference_is_cam2 = reference_is_cam2
    return r


def test_order_swaps_when_cam1_is_right():
    # calibration P2[0,3] > 0 -> tile0 (cam1) is the RIGHT camera; cam2 must be the
    # SGBM left reference so disparities come out positive (not masked away).
    assert _rig(True).order_for_matching("c1", "c2") == ("c2", "c1")


def test_order_keeps_when_standard():
    assert _rig(False).order_for_matching("c1", "c2") == ("c1", "c2")
