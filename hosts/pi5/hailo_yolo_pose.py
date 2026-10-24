#!/usr/bin/env python3
"""HailoYoloPose — yolov8m_pose (17-kpt COCO) person+skeleton on the Hailo-10H (HailoRT-python,
5.3.0). Zoo pre-compiled HEF (yolo26*-pose is exporter-hard-gated, see models/yolov8m_pose_h10.md
"Why not YOLO26-pose"). Same 3-scale (strides 8/16/32) multi-head layout as HailoYolo26, but:

  * box head (64ch) is DFL (4 sides x 16 bins, softmax + weighted integral) — NOT raw ltrb;
  * score head (1ch) is ALREADY sigmoided on-chip — use as-is, do not sigmoid again;
  * keypoint head (51ch = 17 x [x_raw, y_raw, conf_raw]) needs kx=(2*kx_raw+gx)*stride /
    ky=(2*ky_raw+gy)*stride and a host-side sigmoid on conf_raw (NOT baked in on-chip — the
    opposite asymmetry from the score head).

Decode recipe validated twice (models/yolov8m_pose_h10.md): PC-side against Ultralytics' own ONNX
decode graph (<0.0002 px deviation over 8400 anchors x 2 images) and on the deployed HEF on the
bench Pi (anatomically sane multi-person skeletons, bus.jpg/zidane.jpg). `decode_yolo_pose` below
mirrors the card's appendix probe script's math verbatim — it is pure numpy (no hailo_platform
import), so it is importable and unit-testable on x86 with synthetic tensors; `HailoYoloPose` is
the thin HailoRT I/O shell around it (hailo_platform imported lazily, inside __init__, same as
hosts/pi5/hailo_yolo26.py).

LICENCE: yolov8m_pose (Ultralytics) AGPL-3.0 — bench/eval, not-product-cleared.
"""
from __future__ import annotations

from collections import namedtuple

import numpy as np

Det = namedtuple("Det", "class_id label conf x y w h kpts")   # kpts: [[x, y, conf] x 17], source-frame px

IN_SIZE = 640
N_KPTS = 17
# (stride, box-head suffix, score-head suffix, kpt-head suffix) — models/yolov8m_pose_h10.md parse-hef
HEADS = [
    (8,  "conv59", "conv60", "conv61"),
    (16, "conv75", "conv76", "conv77"),
    (32, "conv90", "conv91", "conv92"),
]


def discover_heads(shapes, in_size):
    """Build the (stride, box_name, score_name, kpt_name) table from output shapes alone.

    Per stride the three heads are unambiguous by channel count (box 4*reg_max=64, score 1,
    kpt 17*3=51); stride = in_size / grid. Model-size agnostic — yolov8m/s_pose share the
    layout but not the conv numbering (Task 5 s-variant fallback). Returns full vstream names
    (use with prefix=""), or None if the layout isn't the expected 3x(64,1,51) pyramid.
    """
    by_stride = {}
    for name, shp in shapes.items():
        if len(shp) != 3:
            return None
        h, w, c = shp
        if h != w or h == 0 or in_size % h:
            return None
        by_stride.setdefault(in_size // h, {})[c] = name
    heads = []
    for stride in sorted(by_stride):
        g = by_stride[stride]
        if set(g) != {64, 1, 51}:
            return None
        heads.append((stride, g[64], g[1], g[51]))
    return heads if len(heads) == 3 else None


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def dfl_decode(box_hw64):
    """box_hw64: (H, W, 64) DFL logits -> l, t, r, b each (H, W), in grid-cell units.

    4 sides x reg_max=16 bins per side; softmax over the 16 bins, then the weighted integral
    (sum p * arange(reg_max)) is the distance. Validated against Ultralytics' own DFL.forward
    to <0.0002 px (models/yolov8m_pose_h10.md "PC-side oracle validation").
    """
    H, W, C = box_hw64.shape
    reg_max = C // 4
    x = box_hw64.reshape(H, W, 4, reg_max)
    x = x - x.max(axis=-1, keepdims=True)          # numerical stability
    e = np.exp(x)
    p = e / e.sum(axis=-1, keepdims=True)
    weights = np.arange(reg_max, dtype=np.float32)
    dist = (p * weights).sum(axis=-1)               # (H, W, 4)
    return dist[..., 0], dist[..., 1], dist[..., 2], dist[..., 3]


def _iou(a, b):
    """a, b: (x1, y1, x2, y2)."""
    xa, ya = max(a[0], b[0]), max(a[1], b[1])
    xb, yb = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, xb - xa) * max(0.0, yb - ya)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def decode_yolo_pose(outs, orig_w, orig_h, prefix="", conf_thresh=0.3, iou_thresh=0.5, lb=None,
                      in_w=IN_SIZE, in_h=IN_SIZE, heads=HEADS):
    """outs: {vstream_name: (H, W, C) float32 array, no leading batch dim — matches the existing
    hailo_yolo26.decode_yolo26 / on-HEF probe convention}. Returns list[Det] in original-image
    pixels; each Det.kpts is 17 x [x, y, conf] in the SAME original-image pixel space as the box
    (source-frame convention — never mix with normalized coords).

    lb=(scale, pad_x, pad_y) when the input was letterboxed; None = legacy squash resize. Pure
    function, no hailo/cv2 import — unit-tested on x86 with synthetic tensors
    (test_hailo_yolo_pose.py); this is the exact math validated in models/yolov8m_pose_h10.md.
    """
    if lb is None:
        r_x, r_y, px, py = in_w / orig_w, in_h / orig_h, 0.0, 0.0
    else:
        r_x = r_y = lb[0]
        px, py = lb[1], lb[2]
    raw = []
    for stride, bsfx, ssfx, ksfx in heads:
        box = outs[prefix + bsfx]      # (H, W, 64) DFL logits
        score = outs[prefix + ssfx]    # (H, W, 1) ALREADY sigmoided on-chip -- do not sigmoid again
        kpt = outs[prefix + ksfx]      # (H, W, 51) raw; conf channel needs a host-side sigmoid
        l, t, r, b = dfl_decode(box)
        sc = score[..., 0]
        ys, xs = np.where(sc > conf_thresh)
        for y, x in zip(ys, xs):
            cx, cy = (x + 0.5) * stride, (y + 0.5) * stride
            x1 = ((cx - l[y, x] * stride) - px) / r_x
            y1 = ((cy - t[y, x] * stride) - py) / r_y
            x2 = ((cx + r[y, x] * stride) - px) / r_x
            y2 = ((cy + b[y, x] * stride) - py) / r_y
            x1 = min(max(x1, 0.0), orig_w)
            y1 = min(max(y1, 0.0), orig_h)
            x2 = min(max(x2, 0.0), orig_w)
            y2 = min(max(y2, 0.0), orig_h)
            kraw = kpt[y, x].reshape(N_KPTS, 3)
            kx = ((2.0 * kraw[:, 0] + x) * stride - px) / r_x
            ky = ((2.0 * kraw[:, 1] + y) * stride - py) / r_y
            kconf = _sigmoid(kraw[:, 2])
            kpts = [[float(kx[i]), float(ky[i]), float(kconf[i])] for i in range(N_KPTS)]
            raw.append([float(sc[y, x]), x1, y1, x2, y2, kpts])
    raw.sort(key=lambda d: -d[0])
    keep = []
    for d in raw:
        if all(_iou((d[1], d[2], d[3], d[4]), (k[1], k[2], k[3], k[4])) < iou_thresh for k in keep):
            keep.append(d)
    return [Det(0, "person", d[0], d[1], d[2], d[3] - d[1], d[4] - d[2], d[5]) for d in keep]


class HailoYoloPose:
    """Keeps the vdevice + infer model open; share the vdevice for intra-process co-residency.

    Mirrors HailoYolo26's structure verbatim (letterbox preprocessing, FLOAT32 output vstreams,
    HEF-derived input size) — only the decode differs (DFL box + keypoints, see decode_yolo_pose).
    """

    def __init__(self, hef_path, vdevice=None, conf_thresh=0.3, timeout_ms=5000):
        from hailo_platform import VDevice, FormatType
        self.conf = conf_thresh
        self.timeout_ms = timeout_ms
        self._own = vdevice is None
        self.vd = vdevice if vdevice is not None else VDevice(VDevice.create_params())
        self.im = self.vd.create_infer_model(str(hef_path))
        self.im.set_batch_size(1)
        for on in self.im.output_names:
            self.im.output(on).set_format_type(FormatType.FLOAT32)
        self.cm = self.im.configure()
        self.iname = self.im.input_names[0]
        self.onames = list(self.im.output_names)
        self.prefix = self.onames[0].rsplit("/", 1)[0] + "/" if "/" in self.onames[0] else ""
        self.shapes = {on: tuple(self.im.output(on).shape) for on in self.onames}
        # Read the network's real input size off the HEF (not a hardcoded constant) — same
        # convention as HailoYolo26.
        self.in_h, self.in_w = tuple(self.im.input(self.iname).shape)[:2]
        # Shape-based head discovery first (works for any yolov8*_pose zoo HEF); the named
        # m-variant table is the validated fallback.
        disc = discover_heads(self.shapes, self.in_w)
        if disc is not None:
            self.heads, self._decode_prefix = disc, ""
        else:
            self.heads, self._decode_prefix = HEADS, self.prefix

    def infer(self, bgr):
        import cv2
        if bgr.ndim == 2:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
        h, w = bgr.shape[:2]
        in_w, in_h = self.in_w, self.in_h
        r = min(in_w / w, in_h / h)
        nw, nh = round(w * r), round(h * r)
        px, py = (in_w - nw) // 2, (in_h - nh) // 2
        inp = np.full((in_h, in_w, 3), 114, np.uint8)
        inp[py:py + nh, px:px + nw] = cv2.resize(bgr, (nw, nh))
        inp = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB)
        b = self.cm.create_bindings()
        b.input(self.iname).set_buffer(np.ascontiguousarray(inp, np.uint8))
        outs = {}
        for on in self.onames:
            buf = np.zeros(self.shapes[on], np.float32)
            b.output(on).set_buffer(buf)
            outs[on] = buf
        self.cm.run([b], self.timeout_ms)
        return decode_yolo_pose(outs, w, h, self._decode_prefix, self.conf, lb=(r, px, py),
                                 in_w=in_w, in_h=in_h, heads=self.heads)

    def close(self):
        try:
            self.cm.shutdown()
        except Exception:
            pass
        if self._own:
            try:
                self.vd.release()
            except Exception:
                pass


if __name__ == "__main__":
    import sys
    import cv2
    hef = sys.argv[1] if len(sys.argv) > 1 else "/home/garuda/garuda-sdk-deploy/models/yolov8m_pose_h10.hef"
    imgp = sys.argv[2] if len(sys.argv) > 2 else "/tmp/cm3.jpg"
    img = cv2.imread(imgp)
    if img is None:
        raise SystemExit(f"cannot read {imgp}")
    det = HailoYoloPose(hef)
    for d in det.infer(img)[:10]:
        print(f"  {d.label:12s} {d.conf:.2f}  ({d.x:.0f},{d.y:.0f},{d.w:.0f},{d.h:.0f})")
        for kx, ky, kc in d.kpts[:3]:
            print(f"    kpt {kx:.0f},{ky:.0f} c={kc:.2f}")
