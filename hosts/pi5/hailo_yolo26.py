#!/usr/bin/env python3
"""HailoYolo26 — YOLO26 detection on the Hailo-10H (HailoRT-python, 5.3.0).

YOLO26 is NMS-free/DFL-free: the HEF emits 6 RAW multi-scale tensors — 3 box heads (ltrb
distances, H*W*4) and 3 class heads (logits, H*W*80) at strides 8/16/32 — so we decode here
(anchor-free ltrb*stride, sigmoid on class, light NMS). Validated 2026-08-07: suitcase @0.92
on a CM3 frame, boxes exact. Returns `Detection`s compatible with target_range.select_target.
LICENCE: YOLO26 (Ultralytics) AGPL-3.0 — bench/eval, not-product-cleared.
"""
from __future__ import annotations

import numpy as np

try:
    from target_range import Detection
except Exception:
    from collections import namedtuple
    Detection = namedtuple("Detection", "class_id label conf x y w h")

COCO = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]
# (box head, class head, stride) at the three scales
PAIRS = [("conv71", "conv74", 8), ("conv87", "conv90", 16), ("conv101", "conv104", 32)]
IN_SIZE = 640


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _iou(a, b):
    xa, ya = max(a[2], b[2]), max(a[3], b[3])
    xb, yb = min(a[4], b[4]), min(a[5], b[5])
    inter = max(0.0, xb - xa) * max(0.0, yb - ya)
    ua = (a[4] - a[2]) * (a[5] - a[3]) + (b[4] - b[2]) * (b[5] - b[3]) - inter
    return inter / ua if ua > 0 else 0.0


def decode_yolo26(outs, orig_w, orig_h, prefix="", conf_thresh=0.3, iou_thresh=0.5, lb=None,
                   in_w=IN_SIZE, in_h=IN_SIZE):
    """outs: {vstream_name: float32 array}. Returns list[Detection] in original-image pixels.
    lb=(scale, pad_x, pad_y) when the input was letterboxed; None = legacy squash resize.
    in_w/in_h: the HEF's actual network input size (default 640 for callers that don't pass it;
    HailoYolo26.infer always passes the model's real size, read off the HEF itself)."""
    if lb is None:
        r_x, r_y, px, py = in_w / orig_w, in_h / orig_h, 0.0, 0.0
    else:
        r_x = r_y = lb[0]; px, py = lb[1], lb[2]
    raw = []
    for bsfx, csfx, stride in PAIRS:
        box = outs[prefix + bsfx]        # (h, w, 4) ltrb distances
        cls = outs[prefix + csfx]        # (h, w, 80) logits
        sc = _sigmoid(cls)
        cid = sc.argmax(-1); cf = sc.max(-1)
        ys, xs = np.where(cf > conf_thresh)
        for y, x in zip(ys, xs):
            l, t, r, b = box[y, x]
            cx, cy = (x + 0.5) * stride, (y + 0.5) * stride
            x1 = min(max(((cx - l * stride) - px) / r_x, 0.0), orig_w)
            y1 = min(max(((cy - t * stride) - py) / r_y, 0.0), orig_h)
            x2 = min(max(((cx + r * stride) - px) / r_x, 0.0), orig_w)
            y2 = min(max(((cy + b * stride) - py) / r_y, 0.0), orig_h)
            raw.append([int(cid[y, x]), float(cf[y, x]), x1, y1, x2, y2])
    raw.sort(key=lambda d: -d[1])
    keep = []
    for d in raw:
        if all(not (d[0] == k[0] and _iou(d, k) > iou_thresh) for k in keep):
            keep.append(d)
    return [Detection(d[0], COCO[d[0]] if d[0] < len(COCO) else str(d[0]), d[1],
                      d[2], d[3], d[4] - d[2], d[5] - d[3]) for d in keep]


class HailoYolo26:
    """Keeps the vdevice + infer model open; share the vdevice for intra-process co-residency."""

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
        # Read the network's real input size off the HEF (not a hardcoded constant) — lets the
        # same wrapper serve any square-input YOLO26 export (640, 960, ...) via GARUDA_MONO_YOLO.
        self.in_h, self.in_w = tuple(self.im.input(self.iname).shape)[:2]

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
        return decode_yolo26(outs, w, h, self.prefix, self.conf, lb=(r, px, py))

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
    import cv2
    img = cv2.imread("/tmp/cm3.jpg")
    det = HailoYolo26("/home/garuda/garuda-sdk-deploy/models/yolo26m.hef")
    for d in det.infer(img)[:10]:
        print(f"  {d.label:12s} {d.conf:.2f}  ({d.x:.0f},{d.y:.0f},{d.w:.0f},{d.h:.0f})")
