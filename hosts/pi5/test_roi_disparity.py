import pytest
np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
from roi_disparity import roi_disparity


def test_recovers_known_shift():
    # Rectified convention: a feature at column x in LEFT appears at x-D in RIGHT
    # for disparity D. Synthesize that and check the recovered disparity ~ D.
    rng = np.random.default_rng(0)
    left = (rng.random((120, 320)) * 255).astype(np.uint8)
    D = 16
    right = np.zeros_like(left)
    right[:, : left.shape[1] - D] = left[:, D:]
    d = roi_disparity(left, right, (170, 40, 60, 40), num_disp=32, block=7)
    assert d is not None and abs(d - D) <= 2


def test_none_on_flat_region():
    flat = np.full((120, 320), 128, np.uint8)
    assert roi_disparity(flat, flat, (170, 40, 60, 40)) is None


def test_edge_and_offscreen_bbox_no_crash():
    # Contract: float|None, never a crash — even for edge-hugging / off-screen-origin
    # bboxes (clamped) and tiny bboxes. Guards the Task-3-review Critical/Important findings.
    rng = np.random.default_rng(1)
    left = (rng.random((120, 320)) * 255).astype(np.uint8)
    right = np.zeros_like(left)
    right[:, : left.shape[1] - 12] = left[:, 12:]
    for bbox in [(0, 40, 60, 40), (-5, 40, 60, 40), (315, 40, 40, 40),
                 (170, 40, 2, 40), (170, 118, 60, 40)]:
        out = roi_disparity(left, right, bbox)     # default num_disp=64
        assert out is None or isinstance(out, float)
