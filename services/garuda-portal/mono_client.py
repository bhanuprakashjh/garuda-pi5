"""MonoClient — the portal's subscription onto garuda-mono's state feed.

Reads GET /api/events (SSE, v1 snapshots — hosts/pi5/mono_state.py is the
producer) and republishes each tick on the portal EventBus as TWO events:
`detections` (adapted to the DetectionSet dict FollowPipeline.on_detections
already consumes, so follow/drone-dir works unchanged) and `mono` (the raw
snapshot, for the tracking HUD: distance/lock/cal/engine/detector).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

# COCO index for class_id mapping; labels outside COCO (world-mode prompts) -> -1.
COCO = ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
        "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
        "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
        "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
        "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
        "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
        "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
        "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
        "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
        "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
        "toothbrush"]
_COCO_IDX = {c: i for i, c in enumerate(COCO)}


def snapshot_to_detections(snap: dict) -> dict:
    """mono reports track bboxes in source-frame PIXELS (see hosts/pi5/mono_state.py);
    the DetectionSet contract (interfaces/garuda_interfaces/messages.py) — and every
    consumer of it (garuda-follow's Tracker/target_selector/follow_controller, which
    compare bbox values directly against the 0..1 frame center/edges) — requires
    NORMALIZED [0,1] coordinates, matching what the old garuda-inference child emitted
    from Hailo's own (already-normalized) bbox postprocess. Divide through by the
    snapshot's frame size here so this adapter honors that contract regardless of what
    units the upstream source happens to use internally."""
    frame = snap.get("frame") or {}
    fw = frame.get("width") or 1
    fh = frame.get("height") or 1
    dets = [{"class_id": _COCO_IDX.get(t.get("label"), -1), "label": t.get("label", ""),
             "confidence": float(t.get("conf", 0.0)), "x": float(t.get("x", 0.0)) / fw,
             "y": float(t.get("y", 0.0)) / fh, "w": float(t.get("w", 0.0)) / fw,
             "h": float(t.get("h", 0.0)) / fh}
            for t in snap.get("tracks", [])]
    return {"detections": dets,
            "frame": {"width": fw, "height": fh}}


class MonoClient:
    def __init__(self, bus, base_url: str, retry_s: float = 2.0):
        self.bus = bus
        self.base_url = base_url.rstrip("/")
        self.retry_s = retry_s
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        reported_down = False
        while True:
            try:
                resp = urllib.request.urlopen(self.base_url + "/api/events", timeout=10)
                reported_down = False
                for line in resp:
                    if not line.startswith(b"data: "):
                        continue
                    try:
                        snap = json.loads(line[6:])
                    except ValueError:
                        continue                     # malformed tick: drop, keep the stream
                    self.bus.publish("mono", snap)
                    self.bus.publish("detections", snapshot_to_detections(snap))
            except Exception as e:
                if not reported_down:
                    self.bus.publish("error", {"stage": "mono", "detail": str(e)})
                    reported_down = True
            time.sleep(self.retry_s)
