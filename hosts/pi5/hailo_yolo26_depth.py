#!/usr/bin/env python3
"""Yolo26Depth — YOLO26 monocular METRIC depth on the Hailo-10H (HailoRT-python, 5.3.0).

The HEF is cut at the depth head's final logit conv (Ultralytics `format=hailo` export), so the
host mirrors the tail of `Depth.forward`: depth_m = exp(clip(logit, -4, 5)) ** cal_a * exp(cal_b).
Output is metres directly (unbounded log-depth head, ~0.02-150 m) — no relative->metric fitting.
cal_a/cal_b come from the metadata.yaml the exporter writes next to the HEF (identity if absent).
LICENCE: YOLO26 (Ultralytics) AGPL-3.0 — bench/eval, not-product-cleared.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np


def _load_cal(hef_path):
    """cal_a/cal_b from the exporter's metadata.yaml (same dir as the HEF); identity fallback."""
    meta = Path(hef_path).with_suffix("").parent / "metadata.yaml"
    a, b = 1.0, 0.0
    try:
        for line in meta.read_text().splitlines():
            k, _, v = line.partition(":")
            if k.strip() == "cal_a":
                a = float(v)
            elif k.strip() == "cal_b":
                b = float(v)
    except Exception:
        pass
    return a, b


class Yolo26Depth:
    """Keeps the infer model open; share the vdevice for intra-process co-residency with detect."""

    def __init__(self, hef_path, vdevice=None, timeout_ms=5000):
        from hailo_platform import VDevice, FormatType
        self.timeout_ms = timeout_ms
        self._own = vdevice is None
        self.vd = vdevice if vdevice is not None else VDevice(VDevice.create_params())
        self.im = self.vd.create_infer_model(str(hef_path))
        self.im.set_batch_size(1)
        self.iname = self.im.input_names[0]
        self.oname = self.im.output_names[0]
        self.im.output(self.oname).set_format_type(FormatType.FLOAT32)
        self.cm = self.im.configure()
        self.in_h, self.in_w = tuple(self.im.input(self.iname).shape)[:2]
        self.out_shape = tuple(self.im.output(self.oname).shape)
        self.cal_a, self.cal_b = _load_cal(hef_path)
        self._cal_scale = math.exp(self.cal_b)

    def infer(self, bgr):
        """BGR frame -> metric depth map in metres, at head resolution (in/4, e.g. 192x192)."""
        import cv2
        inp = cv2.cvtColor(cv2.resize(bgr, (self.in_w, self.in_h)), cv2.COLOR_BGR2RGB)
        b = self.cm.create_bindings()
        b.input(self.iname).set_buffer(np.ascontiguousarray(inp, np.uint8))
        logit = np.zeros(self.out_shape, np.float32)
        b.output(self.oname).set_buffer(logit)
        self.cm.run([b], self.timeout_ms)
        depth = np.exp(np.clip(logit[..., 0], -4.0, 5.0))
        if self.cal_a != 1.0:
            depth = depth ** self.cal_a
        return depth * self._cal_scale

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
    import sys, time, cv2
    hef = sys.argv[1] if len(sys.argv) > 1 else "/home/garuda/garuda-sdk-deploy/models/yolo26n-depth.hef"
    img = cv2.imread(sys.argv[2] if len(sys.argv) > 2 else "/tmp/cm3.jpg")
    d = Yolo26Depth(hef)
    print(f"in {d.in_w}x{d.in_h} out {d.out_shape} cal_a={d.cal_a} cal_b={d.cal_b}")
    t0 = time.monotonic(); dep = d.infer(img); dt = time.monotonic() - t0
    print(f"depth map {dep.shape} min={dep.min():.2f}m med={np.median(dep):.2f}m max={dep.max():.2f}m ({dt*1000:.0f} ms)")
