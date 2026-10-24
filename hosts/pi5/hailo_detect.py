#!/usr/bin/env python3
"""yolov8m object detection via HailoRT-python, for running detection on the OV9281
stereo pair inside garuda-depth (Option B: detect + range in one process).

The HEF (/usr/share/hailo-models/yolov8m_h10.hef, same model as garuda-inference) has an
ON-CHIP NMS-by-class postprocess: input UINT8 NHWC 640x640x3, one FLOAT32 output whose
bindings.get_buffer() yields a per-class list of (N,5) arrays [ymin,xmin,ymax,xmax,score]
in NORMALISED coords (confirmed by the Task-1 spike, 2026-08-04; solo ~36 fps on the 10H).

`decode_nms` is pure (numpy only) so it unit-tests with no hardware. cv2 + hailo_platform
are lazy-imported inside HailoDetector so `from hailo_detect import decode_nms` works on any
box. LICENCE: yolov8m is Ultralytics AGPL-3.0 — bench/eval only, not-product-cleared.
"""
from __future__ import annotations

import numpy as np

from target_range import Detection

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

DEFAULT_HEF = "/usr/share/hailo-models/yolov8m_h10.hef"


def decode_nms(raw, labels, in_w, in_h, conf_thresh):
    """Hailo on-chip NMS-by-class output -> Detections in input-image PIXELS.
    `raw`: per-class list; element c is an (N,5) array [ymin,xmin,ymax,xmax,score] normalised."""
    dets = []
    for class_id, arr in enumerate(raw):
        if arr is None or len(arr) == 0:
            continue
        for ymin, xmin, ymax, xmax, score in np.asarray(arr, dtype=np.float32).reshape(-1, 5):
            if score < conf_thresh:
                continue
            label = labels[class_id] if class_id < len(labels) else str(class_id)
            dets.append(Detection(
                class_id, label, float(score),
                float(xmin * in_w), float(ymin * in_h),
                float((xmax - xmin) * in_w), float((ymax - ymin) * in_h)))
    return dets


class HailoDetector:
    """Keeps the device configured + the infer model open across frames. Share ONE VDevice
    across models (pass `vdevice`) — recreating one per model corrupts the Hailo. Mirrors
    HailoStereoNet's lifecycle. `infer(image)` -> list[Detection] in the image's own pixels."""

    def __init__(self, hef_path=DEFAULT_HEF, vdevice=None, conf_thresh=0.4, labels=None,
                 timeout_ms=5000):
        from hailo_platform import VDevice  # lazy: keeps the module importable without Hailo
        self.labels = labels or COCO
        self.conf_thresh = conf_thresh
        self.timeout_ms = timeout_ms
        self._own_vdevice = vdevice is None
        self.vdevice = vdevice if vdevice is not None else VDevice(VDevice.create_params())
        self.im = self.vdevice.create_infer_model(str(hef_path))
        self.im.set_batch_size(1)
        self.cm = self.im.configure()
        self.iname = self.im.input_names[0]
        self.oname = self.im.output_names[0]
        ishape = self.im.input(self.iname).shape          # (H, W, C)
        self.in_h, self.in_w = int(ishape[0]), int(ishape[1])
        # NMS-by-class output frame size (float32 count): from the model, fallback to the
        # yolov8m_h10 max (80 classes * (1 + 100 boxes * 5) = 40080).
        try:
            self.out_floats = int(self.im.output(self.oname).get_frame_size()) // 4
        except Exception:
            self.out_floats = 40080

    def infer(self, image):
        """Run yolov8m on one image (grayscale -> 3ch, resized to the net input). Returns
        list[Detection] with bboxes in the ORIGINAL image's pixel coords."""
        import cv2  # lazy
        if image.ndim == 2:
            image = np.repeat(image[:, :, None], 3, axis=2)
        h, w = image.shape[:2]
        inp = cv2.resize(image, (self.in_w, self.in_h))
        b = self.cm.create_bindings()
        b.input(self.iname).set_buffer(np.ascontiguousarray(inp, dtype=np.uint8))
        b.output(self.oname).set_buffer(np.zeros((self.out_floats,), dtype=np.float32))
        self.cm.run([b], self.timeout_ms)
        raw = b.output(self.oname).get_buffer()
        return decode_nms(raw, self.labels, w, h, self.conf_thresh)

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
