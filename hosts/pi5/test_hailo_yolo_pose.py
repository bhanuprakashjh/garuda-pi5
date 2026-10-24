"""x86 unit tests for hailo_yolo_pose's pure decode math (no hailo_platform, no cv2, no NPU).

Synthetic tensors constructed so the expected output is hand-computable:
  (a) a degenerate (near one-hot) DFL distribution whose softmax-integral is known exactly ->
      expected box.
  (b) a synthetic keypoint vector -> expected x2/grid/stride/letterbox-unmapped pixel + sigmoided
      conf.
  (c) score-head passthrough: the score value must NOT be re-sigmoided (the card's documented
      on-chip/host asymmetry, opposite of the keypoint-confidence channel).

Compensates for the deferred on-Pi HEF verify (controller amendment, no-Pi phase) — these tests
pin down the exact math in models/yolov8m_pose_h10.md against hand-computed expectations.
"""
import math

import numpy as np

from hailo_yolo_pose import Det, decode_yolo_pose, dfl_decode


def _one_hot_dfl_box(l_bin, t_bin, r_bin, b_bin, reg_max=16, big=100.0):
    """Build a (1, 1, 4*reg_max) DFL logits tensor whose softmax is (to float64 precision) a
    one-hot at the given bin per side -> dfl_decode's weighted integral is exactly that bin index.
    """
    box = np.zeros((1, 1, 4 * reg_max), np.float64)
    d = box.reshape(1, 1, 4, reg_max)
    for side, bin_idx in enumerate((l_bin, t_bin, r_bin, b_bin)):
        d[0, 0, side, bin_idx] = big
    return box


def test_dfl_decode_one_hot_integral():
    box = _one_hot_dfl_box(2, 5, 1, 8)
    l, t, r, b = dfl_decode(box)
    assert math.isclose(l[0, 0], 2.0, abs_tol=1e-6)
    assert math.isclose(t[0, 0], 5.0, abs_tol=1e-6)
    assert math.isclose(r[0, 0], 1.0, abs_tol=1e-6)
    assert math.isclose(b[0, 0], 8.0, abs_tol=1e-6)


def _single_cell_outs(l_bin, t_bin, r_bin, b_bin, score, kpt17x3, reg_max=16):
    """One stride-8 head, one grid cell (x=5, y=5), everything else empty."""
    box = _one_hot_dfl_box(l_bin, t_bin, r_bin, b_bin, reg_max).astype(np.float32)
    sc = np.zeros((1, 1, 1), np.float32)
    sc[0, 0, 0] = score
    kpt = np.zeros((1, 1, 51), np.float32)
    kpt[0, 0] = np.asarray(kpt17x3, np.float32).reshape(51)
    return {"box": box, "score": sc, "kpt": kpt}


HEADS_SINGLE = [(8, "box", "score", "kpt")]


def test_box_decode_from_known_dfl_distribution():
    # grid cell (x=5, y=5), stride=8 -> cx=cy=44.0; l=2,t=5,r=1,b=8 (grid units) -> box in px.
    kpt17x3 = [[0.0, 0.0, 0.0]] * 17
    outs = _single_cell_outs(2, 5, 1, 8, score=0.9, kpt17x3=kpt17x3)
    # place the hot cell at (x=5, y=5): pad the (1,1,C) arrays out to (6,6,C) with the real
    # values at [5,5] and everything else at -inf-ish (score 0 elsewhere -> below threshold).
    outs = _place_at(outs, 5, 5, grid=6)
    dets = decode_yolo_pose(outs, orig_w=10000, orig_h=10000, heads=HEADS_SINGLE,
                             conf_thresh=0.5, lb=(1.0, 0.0, 0.0), in_w=10000, in_h=10000)
    assert len(dets) == 1
    d = dets[0]
    assert isinstance(d, Det)
    assert d.label == "person"
    assert math.isclose(d.conf, 0.9, abs_tol=1e-5)
    # cx=cy=(5+0.5)*8=44; x1=44-2*8=28, y1=44-5*8=4, x2=44+1*8=52, y2=44+8*8=108
    assert math.isclose(d.x, 28.0, abs_tol=1e-3)
    assert math.isclose(d.y, 4.0, abs_tol=1e-3)
    assert math.isclose(d.w, 52.0 - 28.0, abs_tol=1e-3)
    assert math.isclose(d.h, 108.0 - 4.0, abs_tol=1e-3)


def _place_at(outs, gx, gy, grid):
    """Expand each (1,1,C) tensor in `outs` to (grid,grid,C) with the real values at [gy,gx] and
    zeros (safely below any conf_thresh > 0) elsewhere."""
    placed = {}
    for name, arr in outs.items():
        c = arr.shape[-1]
        full = np.zeros((grid, grid, c), arr.dtype)
        full[gy, gx] = arr[0, 0]
        placed[name] = full
    return placed


def test_keypoint_decode_scale_grid_stride_and_sigmoid():
    # nose (kpt 0): kx_raw=0.1, ky_raw=0.2, kc_raw=2.0; rest zero.
    kpt17x3 = [[0.1, 0.2, 2.0]] + [[0.0, 0.0, 0.0]] * 16
    outs = _single_cell_outs(2, 5, 1, 8, score=0.9, kpt17x3=kpt17x3)
    outs = _place_at(outs, 5, 5, grid=6)
    dets = decode_yolo_pose(outs, orig_w=10000, orig_h=10000, heads=HEADS_SINGLE,
                             conf_thresh=0.5, lb=(1.0, 0.0, 0.0), in_w=10000, in_h=10000)
    assert len(dets) == 1
    kpts = dets[0].kpts
    assert len(kpts) == 17
    # kx = (2*0.1 + 5) * 8 = 41.6; ky = (2*0.2 + 5) * 8 = 43.2; kconf = sigmoid(2.0)
    nose_x, nose_y, nose_c = kpts[0]
    assert math.isclose(nose_x, 41.6, abs_tol=1e-3)
    assert math.isclose(nose_y, 43.2, abs_tol=1e-3)
    assert math.isclose(nose_c, 1.0 / (1.0 + math.exp(-2.0)), abs_tol=1e-6)
    # an all-zero-raw keypoint: kx = ky = (0 + 5) * 8 = 40; kconf = sigmoid(0) = 0.5
    other_x, other_y, other_c = kpts[1]
    assert math.isclose(other_x, 40.0, abs_tol=1e-3)
    assert math.isclose(other_y, 40.0, abs_tol=1e-3)
    assert math.isclose(other_c, 0.5, abs_tol=1e-6)


def test_score_head_used_as_is_not_double_sigmoided():
    # score=0.7 is already a probability (on-chip sigmoid) -- decode must use it verbatim.
    # If the wrapper mistakenly re-sigmoided it, conf would read sigmoid(0.7)=~0.668, not 0.7.
    kpt17x3 = [[0.0, 0.0, 0.0]] * 17
    outs = _single_cell_outs(0, 0, 0, 0, score=0.7, kpt17x3=kpt17x3)
    outs = _place_at(outs, 0, 0, grid=1)
    dets = decode_yolo_pose(outs, orig_w=10000, orig_h=10000, heads=HEADS_SINGLE,
                             conf_thresh=0.5, lb=(1.0, 0.0, 0.0), in_w=10000, in_h=10000)
    assert len(dets) == 1
    assert math.isclose(dets[0].conf, 0.7, abs_tol=1e-6)
    double_sigmoid = 1.0 / (1.0 + math.exp(-0.7))
    assert not math.isclose(dets[0].conf, double_sigmoid, abs_tol=1e-3)


def test_letterbox_unmapping_matches_hailo_yolo26_convention():
    # same (r, px, py) semantics as hosts/pi5/hailo_yolo26.decode_yolo26: subtract padding,
    # divide by scale. r=0.5, px=40, py=0 (a wide source frame letterboxed into a square net).
    kpt17x3 = [[0.0, 0.0, 5.0]] + [[0.0, 0.0, 0.0]] * 16
    outs = _single_cell_outs(1, 1, 1, 1, score=0.9, kpt17x3=kpt17x3)
    outs = _place_at(outs, 10, 10, grid=11)
    dets = decode_yolo_pose(outs, orig_w=1000, orig_h=500, heads=HEADS_SINGLE,
                             conf_thresh=0.5, lb=(0.5, 40.0, 0.0), in_w=640, in_h=640)
    assert len(dets) == 1
    d = dets[0]
    # stride=8, cell (10,10) -> cx=cy=84.0; l=t=r=b=1 grid unit -> box in net space
    # (76,76)-(92,92); unletterbox: (x-40)/0.5, y/0.5
    assert math.isclose(d.x, (76.0 - 40.0) / 0.5, abs_tol=1e-3)
    assert math.isclose(d.y, 76.0 / 0.5, abs_tol=1e-3)
    # nose kpt: kx=(0+10)*8=80 -> (80-40)/0.5=80; ky=(0+10)*8=80 -> 80/0.5=160
    nose_x, nose_y, _ = d.kpts[0]
    assert math.isclose(nose_x, (80.0 - 40.0) / 0.5, abs_tol=1e-3)
    assert math.isclose(nose_y, 80.0 / 0.5, abs_tol=1e-3)


def test_discover_heads_maps_by_shape_not_name():
    from hailo_yolo_pose import discover_heads
    # m-variant layout (conv59 = 80x80 BOX head)
    shapes_m = {
        "yolov8m_pose/conv59": (80, 80, 64), "yolov8m_pose/conv60": (80, 80, 1), "yolov8m_pose/conv61": (80, 80, 51),
        "yolov8m_pose/conv75": (40, 40, 64), "yolov8m_pose/conv76": (40, 40, 1), "yolov8m_pose/conv77": (40, 40, 51),
        "yolov8m_pose/conv90": (20, 20, 64), "yolov8m_pose/conv91": (20, 20, 1), "yolov8m_pose/conv92": (20, 20, 51),
    }
    h = discover_heads(shapes_m, 640)
    assert [x[0] for x in h] == [8, 16, 32]
    assert h[0][1].endswith("conv59") and h[0][2].endswith("conv60") and h[0][3].endswith("conv61")
    # s-variant layout: conv59 is the 40x40 KPT head there — name-based mapping would mis-assign
    # (real zoo layout, hailortcli parse-hef 2026-08-10)
    shapes_s = {
        "yolov8s_pose/conv43": (80, 80, 64), "yolov8s_pose/conv44": (80, 80, 1), "yolov8s_pose/conv45": (80, 80, 51),
        "yolov8s_pose/conv57": (40, 40, 64), "yolov8s_pose/conv58": (40, 40, 1), "yolov8s_pose/conv59": (40, 40, 51),
        "yolov8s_pose/conv70": (20, 20, 64), "yolov8s_pose/conv71": (20, 20, 1), "yolov8s_pose/conv72": (20, 20, 51),
    }
    h = discover_heads(shapes_s, 640)
    assert [x[0] for x in h] == [8, 16, 32]
    assert h[1][3].endswith("conv59")          # 40x40 kpt head, NOT a box head
    assert h[2][1].endswith("conv70")          # 20x20 box head
    # malformed layouts -> None (falls back to the named table)
    assert discover_heads({"a": (80, 80, 64)}, 640) is None
    assert discover_heads({"a": (80, 80, 64), "b": (80, 80, 2), "c": (80, 80, 51)}, 640) is None
