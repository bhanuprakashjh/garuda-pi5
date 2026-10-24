import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))  # garuda_interfaces
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))                  # track_view

from track_view import draw_overlay, display_to_frame  # noqa: E402
from garuda_interfaces.tracked_objects import (         # noqa: E402
    TrackedObjects, TrackedObject, LockState, ObjState)


def test_display_to_frame_scale_and_clamp():
    assert display_to_frame(640, 480, 1280, 960, 640, 480) == (320, 240)  # half-scale render
    assert display_to_frame(0, 0, 1280, 960, 640, 480) == (0, 0)
    assert display_to_frame(5000, 5000, 1280, 960, 640, 480) == (639, 479)  # clamped in-bounds


def test_draw_overlay_locked_and_empty():
    import pytest
    pytest.importorskip("cv2")  # drawing needs OpenCV (present on the bench, not on CI)
    f = np.zeros((640, 640, 3), np.uint8)
    locked = TrackedObjects(0, 640, 640,
        [TrackedObject(3, 0, "person", 0.9, [10, 10, 50, 50], 5, ObjState.CONFIRMED)],
        locked_id=3, lock_state=LockState.LOCKED, seq=1)
    out = draw_overlay(f.copy(), locked)
    assert out.shape == f.shape and int(out.sum()) > 0        # boxes + banner drawn

    empty = TrackedObjects(0, 640, 640, [], -1, LockState.SEARCHING, 1)
    out2 = draw_overlay(f.copy(), empty)
    assert out2.shape == f.shape and int(out2.sum()) > 0      # status banner still drawn
