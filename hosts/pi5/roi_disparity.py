"""Bbox-ROI stereo disparity: block-match only a band around the target so the
cost stays tiny (no full-frame dense map). Returns median valid disparity (px)."""
import cv2
import numpy as np

_sgbm_cache = {}


def _sgbm(num_disp, block):
    key = (num_disp, block)
    if key not in _sgbm_cache:
        _sgbm_cache[key] = cv2.StereoSGBM_create(
            minDisparity=0, numDisparities=num_disp, blockSize=block,
            P1=8 * block * block, P2=32 * block * block, uniquenessRatio=10)
    return _sgbm_cache[key]


def roi_disparity(rect_left, rect_right, bbox, num_disp=64, block=7, min_valid=40):
    x, y, w, h = (int(round(v)) for v in bbox)
    x, y = max(0, x), max(0, y)        # bbox may extend off the top/left edge -> clamp
    H, W = rect_left.shape[:2]
    x0 = max(0, x - num_disp)          # left-pad for the search range
    x1 = min(W, x + w)
    y0, y1 = y, min(H, y + h)
    if (x1 - x0) < block or (y1 - y0) < block:
        return None
    try:
        disp = _sgbm(num_disp, block).compute(
            rect_left[y0:y1, x0:x1], rect_right[y0:y1, x0:x1]).astype(np.float32) / 16.0
    except cv2.error:                  # SGBM input-size constraints vary by cv2 version
        return None
    bx0 = x - x0                       # >= 0 after the clamp above
    roi = disp[:, bx0:bx0 + w]
    valid = roi[roi > 0]
    if valid.size < min_valid:
        return None
    return float(np.median(valid))
