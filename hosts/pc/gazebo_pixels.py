"""RGBD adapter reused from Hailo-15 bench/sim/gazebo_vision.py at b57dd5e.
Pixel/depth timestamps are paired; no actor poses enter perception.
"""
import io
import threading
import time
from collections import OrderedDict
import numpy as np
from PIL import Image

def jpeg(rgb):
    out=io.BytesIO(); Image.fromarray(rgb).save(out,'JPEG',quality=83); return out.getvalue()

def stamp(msg):
    return msg.header.stamp.sec * 1_000_000_000 + msg.header.stamp.nsec


def rgb_message(msg):
    if msg.pixel_format_type != 3 or msg.step < msg.width*3 or len(msg.data) != msg.step*msg.height:
        raise ValueError("expected packed Gazebo RGB_INT8")
    return np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.step)[:, :msg.width*3].reshape(msg.height, msg.width, 3)


def depth_range(depth, box):
    """Central torso median. Aligned RGBD pixels only; invalid/dispersed = no range."""
    if depth is None or box is None:
        return None
    x, y, w, h = (box[k] for k in ("x", "y", "w", "h"))
    if not all(np.isfinite(v) for v in (x, y, w, h)) or w < 4 or h < 8:
        return None
    crop = depth[max(0, int(y+h*.25)):min(720, int(y+h*.6)),
                 max(0, int(x+w*.3)):min(1280, int(x+w*.7))]
    valid = crop[np.isfinite(crop) & (crop > .3) & (crop < 30)]
    if crop.size < 8 or valid.size < crop.size*.7:
        return None
    lo, med, hi = np.percentile(valid, (20, 50, 80))
    return float(med) if hi-lo < max(.4, .15*med) else None


class GazeboPixels:
    def __init__(self):
        from gz.transport13 import Node
        from gz.msgs10.image_pb2 import Image as GzImage
        from gz.msgs10.clock_pb2 import Clock
        self.lock = threading.Lock()
        self.latest = self.overview = None
        self.depths = OrderedDict()
        self.sequence = self.invalid = 0
        self.clock = None
        self.previous_clock = None
        self.rtf = None
        self.node = Node()
        for typ, topic, cb in ((GzImage, "/garuda/drone/image", self.on_rgb),
                               (GzImage, "/garuda/drone/depth_image", self.on_depth),
                               (GzImage, "/garuda/overview", self.on_overview),
                               (Clock, "/world/garuda_lab/clock", self.on_clock)):
            if not self.node.subscribe(typ, topic, cb):
                raise RuntimeError("could not subscribe " + topic)

    def on_clock(self, msg):
        value = msg.sim.sec*1e9+msg.sim.nsec
        now = time.monotonic_ns()
        with self.lock:
            self.clock = (value, now)
            if self.previous_clock is None:
                self.previous_clock = self.clock
            elif now-self.previous_clock[1] > 1e9:
                self.rtf = (value-self.previous_clock[0])/(now-self.previous_clock[1])
                self.previous_clock = self.clock

    def on_rgb(self, msg):
        received = time.monotonic_ns()
        try:
            rgb = rgb_message(msg)
            if rgb.shape != (720, 1280, 3):
                raise ValueError("camera must be 1280x720")
            with self.lock:
                value = stamp(msg)
                if self.latest and value <= self.latest[1]:
                    self.invalid += 1
                    return
                self.sequence += 1
                self.latest = (self.sequence, value, received, rgb)
        except ValueError:
            self.invalid += 1

    def on_depth(self, msg):
        if (msg.width, msg.height, msg.pixel_format_type, msg.step) != (1280, 720, 13, 5120):
            return
        if len(msg.data) != 1280*720*4:
            return
        with self.lock:
            self.depths[stamp(msg)] = np.frombuffer(msg.data, dtype="<f4").reshape(720, 1280)
            while len(self.depths) > 4:
                self.depths.popitem(last=False)

    def on_overview(self, msg):
        try:
            value = (time.monotonic_ns(), jpeg(rgb_message(msg)))
            with self.lock:
                self.overview = value
        except ValueError:
            self.invalid += 1

    def snapshot(self):
        with self.lock:
            frame = self.latest
            return frame, self.depths.get(frame[1]) if frame else None, self.clock, self.rtf
