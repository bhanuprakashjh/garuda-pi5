#!/usr/bin/env python3
"""HailoYoloSeg — YOLOv5n-seg instance segmentation on the Hailo-10H. Pi-specific (HailoRT).

The HEF (yolov5n_seg_h10.hef) emits RAW YOLOv5-seg tensors, not finished masks, so the decode
runs on the CPU here:
  input   640x640x3 UINT8
  conv63  160x160x32   mask prototypes
  conv61  20x20x351  \\
  conv55  40x40x351   > detection heads (351 = 3 anchors x 117; 117 = 4 box +1 obj +80 cls +32 coeff)
  conv48  80x80x351  /
Decode = grid/anchor box decode -> NMS -> per-instance mask = sigmoid(coeff . protos), box-cropped.

BENCH / EVALUATION (CLAUDE.md rule 6): YOLOv5 is GPL-3.0 (Ultralytics). Bench/experimental only;
consumed behind IFrontEnd, tagged not-product-cleared.

    python3 hosts/pi5/hailo_yolo_seg.py   # probe a captured tile, report dets + fps
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

import cv2
import numpy as np
from hailo_platform import FormatType, VDevice

IN_W, IN_H = 640, 640

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


def to_seg_input(img):
    """A camera tile (grayscale 2D or BGR 3D) -> (640,640,3) uint8 RGB. Plain resize (no letterbox):
    masks decoded here map back to the tile via a single inverse resize, so the aspect distortion
    cancels and no letterbox bookkeeping is needed."""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    else:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return cv2.resize(img, (IN_W, IN_H))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


class HailoYoloSeg:
    def __init__(self, hef_path, json_path, score_thr=None, iou_thr=None,
                 timeout_ms: int = 5000, vdevice=None):
        cfg = json.loads(pathlib.Path(json_path).read_text()) if json_path else {}
        self.score_thr = float(score_thr if score_thr is not None else cfg.get("score_threshold", 0.25))
        self.iou_thr = float(iou_thr if iou_thr is not None else cfg.get("iou_threshold", 0.6))
        # stride -> its 3 (w,h) anchors, from the json (order: stride32, stride16, stride8)
        strides = cfg.get("strides", [32, 16, 8])
        raw_anchors = cfg.get("anchors", [[116, 90, 156, 198, 373, 326],
                                          [30, 61, 62, 45, 59, 119],
                                          [10, 13, 16, 30, 33, 23]])
        self.anchors_by_stride = {}
        for s, a in zip(strides, raw_anchors):
            self.anchors_by_stride[s] = [(a[i], a[i + 1]) for i in range(0, 6, 2)]

        self.timeout_ms = timeout_ms
        self._own_vdevice = vdevice is None
        self.vdevice = vdevice if vdevice is not None else VDevice(VDevice.create_params())
        self.im = self.vdevice.create_infer_model(str(hef_path))
        self.im.set_batch_size(1)
        self.in_name = self.im.input_names[0]
        self.out_names = list(self.im.output_names)
        for n in self.out_names:                       # dequantize on-device -> float32 buffers
            self.im.output(n).set_format_type(FormatType.FLOAT32)
        self.cm = self.im.configure()
        self.out_shapes = {n: tuple(self.im.output(n).shape) for n in self.out_names}
        # identify proto (C==32) vs the three detection heads (C==351), keyed by grid size S
        self.proto_name = next(n for n in self.out_names if self.out_shapes[n][-1] == 32)
        self.det_names = [n for n in self.out_names if self.out_shapes[n][-1] != 32]

    def infer(self, rgb640):
        b = self.cm.create_bindings()
        b.input(self.in_name).set_buffer(np.ascontiguousarray(rgb640, dtype=np.uint8))
        outs = {}
        for n in self.out_names:
            buf = np.zeros(self.out_shapes[n], dtype=np.float32)
            b.output(n).set_buffer(buf)
            outs[n] = buf
        self.cm.run([b], self.timeout_ms)
        return outs

    def _decode_head(self, t, stride):
        # Exact objectness gate: final score = obj*cls <= obj, so a cell can only survive
        # score_thr if obj > score_thr. Gate on obj FIRST and decode only the survivors — this
        # loses nothing and avoids sigmoid over 80 classes x every cell (the CPU-decode cost).
        S = t.shape[0]
        t = t.reshape(S, S, 3, 117)
        obj = _sigmoid(t[..., 4])                       # (S,S,3) — the only full-grid sigmoid
        cand = obj > self.score_thr
        if not cand.any():
            return None
        rows, cols, anch = np.nonzero(cand)             # (M,) each
        tc = t[rows, cols, anch]                        # (M,117)
        xy = _sigmoid(tc[:, 0:2]); wh = _sigmoid(tc[:, 2:4])
        cls = _sigmoid(tc[:, 5:85]); coef = tc[:, 85:117]
        anchor_arr = np.asarray(self.anchors_by_stride[stride], dtype=np.float32)   # (3,2)
        aw = anchor_arr[anch, 0]; ah = anchor_arr[anch, 1]
        cx = (xy[:, 0] * 2 - 0.5 + cols) * stride
        cy = (xy[:, 1] * 2 - 0.5 + rows) * stride
        bw = (wh[:, 0] * 2) ** 2 * aw
        bh = (wh[:, 1] * 2) ** 2 * ah
        scores = obj[rows, cols, anch][:, None] * cls   # (M,80)
        cls_id = scores.argmax(1); cls_sc = scores.max(1)
        keep = cls_sc > self.score_thr
        if not keep.any():
            return None
        boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)[keep]
        return boxes, cls_id[keep], cls_sc[keep], coef[keep]    # (M,4),(M,),(M,),(M,32)

    def infer_instances(self, rgb640, dynamic_only=None):
        """Returns list of (class_id, class_name, box_xyxy@640, inst_mask uint8 @160x160).
        If dynamic_only (set of names) is given, masks are assembled only for those classes."""
        outs = self.infer(rgb640)
        boxes, ids, scs, cfs = [], [], [], []
        for n in self.det_names:
            t = outs[n]
            S = t.shape[0]
            stride = IN_W // S
            d = self._decode_head(t, stride)
            if d is None:
                continue
            b, i, s, c = d
            boxes.append(b); ids.append(i); scs.append(s); cfs.append(c)
        if not boxes:
            return []
        boxes = np.concatenate(boxes); ids = np.concatenate(ids)
        scs = np.concatenate(scs); cfs = np.concatenate(cfs)
        # class-agnostic NMS (union of masks anyway)
        wh_boxes = np.stack([boxes[:, 0], boxes[:, 1],
                             boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1]], axis=1)
        idx = cv2.dnn.NMSBoxes(wh_boxes.tolist(), scs.tolist(), self.score_thr, self.iou_thr)
        if len(idx) == 0:
            return []
        idx = np.array(idx).reshape(-1)
        proto = outs[self.proto_name].reshape(-1, 32)           # (25600,32)
        results = []
        for i in idx:
            name = COCO[int(ids[i])] if int(ids[i]) < len(COCO) else str(int(ids[i]))
            box = boxes[i]
            if dynamic_only is not None and name not in dynamic_only:
                results.append((int(ids[i]), name, box, None))
                continue
            m = _sigmoid(proto @ cfs[i]).reshape(160, 160)
            x1, y1, x2, y2 = np.clip(np.round(box / 4).astype(int), 0, 160)
            crop = np.zeros((160, 160), np.uint8)
            crop[y1:y2, x1:x2] = (m[y1:y2, x1:x2] > 0.5).astype(np.uint8)
            results.append((int(ids[i]), name, box, crop))
        return results

    def close(self):
        try:
            self.cm.shutdown()
        except Exception:
            pass
        if self._own_vdevice:
            try:
                self.vdevice.release()
            except Exception:
                pass


def _probe():
    repo = pathlib.Path(__file__).resolve().parents[2]
    caps = repo / "calib" / "captures"
    tile = cv2.imread(str(caps / "pair11_L.png"), cv2.IMREAD_GRAYSCALE)
    if tile is None:
        raise SystemExit(f"no probe image at {caps/'pair11_L.png'}")
    net = HailoYoloSeg(repo / "models" / "yolov5n_seg_h10.hef"
                       if (repo / "models" / "yolov5n_seg_h10.hef").exists()
                       else "/usr/share/hailo-models/yolov5n_seg_h10.hef",
                       "/usr/share/hailo-models/yolov5seg.json")
    rgb = to_seg_input(tile)
    for _ in range(2):
        dets = net.infer_instances(rgb)
    n, t0 = 10, time.monotonic()
    for _ in range(n):
        dets = net.infer_instances(rgb)
    dt = (time.monotonic() - t0) / n
    print(f"detections={len(dets)} classes={sorted({d[1] for d in dets})}")
    print(f"infer={1e3*dt:.1f} ms ({1/dt:.1f} fps)  score_thr={net.score_thr} iou_thr={net.iou_thr}")
    net.close()


if __name__ == "__main__":
    _probe()
    sys.exit(0)
