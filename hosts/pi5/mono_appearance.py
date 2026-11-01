"""Small clothing-colour descriptors for demo reacquisition, not face recognition.

Computed from the same frame as detection. Descriptors remain in process/SSE memory;
no person images, identity names or persistent galleries are stored.
"""
import cv2
import numpy as np


def describe_people(image, tracks):
    height, width = image.shape[:2]
    result = {}
    for t in tracks:
        if t.cls != "person" or t.time_since_update != 0 or t.conf < 0.5:
            continue
        x, y, w, h = map(float, t.bbox)
        if not all(np.isfinite(v) for v in (x, y, w, h)) or w < 30 or h < 60:
            continue
        # Ignore horizontal boundary fragments; wait for a usable returning view.
        if x < 0 or x + w > width or y < 0:
            continue
        x0, x1 = int(x + .2*w), int(x + .8*w)
        y0, y1 = int(y + .2*h), min(height, int(y + .85*h))
        if x1 <= x0 or y1-y0 < 24:
            continue
        crop = cv2.resize(image[y0:y1, x0:x1], (16, 32), interpolation=cv2.INTER_AREA)
        parts = []
        for band in (crop[:16], crop[16:]):
            bins = band.astype(np.int32) // 64
            index = bins[:, :, 0]*16 + bins[:, :, 1]*4 + bins[:, :, 2]
            hist = np.bincount(index.ravel(), minlength=64).astype(float)
            parts.extend(np.sqrt(hist / (2*hist.sum())).round(5).tolist())
        result[t.track_id] = parts
    return result
