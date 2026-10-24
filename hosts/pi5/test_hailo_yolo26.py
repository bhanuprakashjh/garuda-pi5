"""Unit tests for the hardware-free parts of hosts/pi5/hailo_yolo26.py.

`decode_yolo26` is the anchor-free ltrb-distance decode + letterbox-inverse + light NMS. Task 8
(portal-tracking) made the network input size a parameter (`in_w`/`in_h`, read off the HEF by
`HailoYolo26.__init__` instead of a hardcoded 640) so the same wrapper can serve a 960sq export
alongside the stock 640 one — this is exactly the kind of change that is easy to get silently
wrong, so it runs on x86 CI here, mirroring test_hailo_yolo_world.py's
test_decode_maps_a_letterboxed_box_back_to_original_pixels. The HEF/NPU path
(HailoYolo26.__init__/.infer) needs the bench Pi and is not covered. Numbers here are
constructed, NOT measured — see bench/benchlog.md.
"""
import numpy as np
import pytest

from hailo_yolo26 import COCO, IN_SIZE, PAIRS, decode_yolo26


def _logit(p):
    return float(np.log(p / (1.0 - p)))


def _single_hit_outs(stride, class_id, score):
    """6-tensor HailoRT-shaped output dict (3 box heads + 3 class heads, all 1x1 grids): exactly
    one cell, at `stride`, scores `class_id` at `score`; the other two strides' single cell sits
    at logit -50 (sigmoid ~1e-22) for every class, well under any conf_thresh used below, so they
    can never contribute a spurious detection."""
    outs = {}
    for bsfx, csfx, s in PAIRS:
        outs[bsfx] = np.zeros((1, 1, 4), np.float32)
        cls = np.full((1, 1, 80), -50.0, np.float32)
        if s == stride:
            cls[0, 0, class_id] = _logit(score)
        outs[csfx] = cls
    return outs


def _place_box(outs, stride, cx, cy, x0, y0, x1, y1):
    """Solve the ltrb distances at grid cell (0,0)/`stride` so decode_yolo26's anchor-free
    formula reconstructs exactly the letterboxed-space box (x0,y0,x1,y1)."""
    bsfx = next(b for b, c, s in PAIRS if s == stride)
    outs[bsfx][0, 0] = ((cx - x0) / stride, (cy - y0) / stride,
                         (x1 - cx) / stride, (y1 - cy) / stride)


def test_decode_maps_a_letterboxed_box_back_to_original_pixels_at_640():
    # 1280x720 -> 640x640 letterbox (the stock path): scale 0.5, pad_x 0, pad_y (640-360)//2=140
    r_scale, px, py = 0.5, 0, 140
    stride = 8
    cx = cy = (0 + 0.5) * stride
    x0, y0 = 100 * r_scale + px, 100 * r_scale + py
    x1, y1 = 500 * r_scale + px, 300 * r_scale + py
    outs = _single_hit_outs(stride, class_id=0, score=0.9)
    _place_box(outs, stride, cx, cy, x0, y0, x1, y1)

    d = decode_yolo26(outs, 1280, 720, conf_thresh=0.3, lb=(r_scale, px, py))

    assert len(d) == 1
    assert d[0].label == "person" and d[0].class_id == 0
    assert d[0].conf == pytest.approx(0.9, abs=1e-4)
    assert (d[0].x, d[0].y, d[0].w, d[0].h) == pytest.approx((100, 100, 400, 200), abs=1e-3)


def test_decode_maps_a_letterboxed_box_back_to_original_pixels_at_960():
    # SAME original image and SAME target box as the 640 test above, but the network is 960x960
    # (Task 8's export) -> a genuinely different scale/pad (0.75, 0, 210 vs 0.5, 0, 140). Proves
    # the letterbox-inverse generalizes to a non-640 net, not just the stock one.
    r_scale, px, py = 0.75, 0, 210  # 1280x720 -> 960x960: scale 0.75, pad_y (960-540)//2=210
    stride = 8
    cx = cy = (0 + 0.5) * stride
    x0, y0 = 100 * r_scale + px, 100 * r_scale + py
    x1, y1 = 500 * r_scale + px, 300 * r_scale + py
    outs = _single_hit_outs(stride, class_id=0, score=0.9)
    _place_box(outs, stride, cx, cy, x0, y0, x1, y1)

    d = decode_yolo26(outs, 1280, 720, conf_thresh=0.3, lb=(r_scale, px, py))

    assert len(d) == 1
    assert d[0].label == "person" and d[0].class_id == 0
    assert (d[0].x, d[0].y, d[0].w, d[0].h) == pytest.approx((100, 100, 400, 200), abs=1e-3)


def test_decode_squash_resize_honours_explicit_in_w_in_h():
    # lb=None (the "legacy squash resize" branch): in_w/in_h -- the kwargs Task 8 added -- drive
    # the per-axis scale directly instead of the module-level IN_SIZE constant. Same target box,
    # decoded once at the (omitted) 640 default and once with 960 passed explicitly.
    stride = 8
    cx = cy = (0 + 0.5) * stride
    orig_w, orig_h = 1280, 720

    for in_size, pass_explicitly in ((640, False), (960, True)):
        r_x, r_y = in_size / orig_w, in_size / orig_h
        x0, y0 = 100 * r_x, 100 * r_y
        x1, y1 = 500 * r_x, 300 * r_y
        outs = _single_hit_outs(stride, class_id=2, score=0.6)
        _place_box(outs, stride, cx, cy, x0, y0, x1, y1)
        kwargs = {"in_w": in_size, "in_h": in_size} if pass_explicitly else {}

        d = decode_yolo26(outs, orig_w, orig_h, conf_thresh=0.3, lb=None, **kwargs)

        assert len(d) == 1
        assert d[0].label == "car" and d[0].class_id == 2
        assert (d[0].x, d[0].y, d[0].w, d[0].h) == pytest.approx((100, 100, 400, 200), abs=1e-3)


def test_decode_yolo26_default_in_size_is_still_640():
    assert IN_SIZE == 640  # the stock HEF's size; GARUDA_MONO_YOLO overrides the HEF, not this
