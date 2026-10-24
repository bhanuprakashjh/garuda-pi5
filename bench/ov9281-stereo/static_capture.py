#!/usr/bin/env python3
"""Capture N stereo pairs from the stationary OV9281 CamArray for the desync test.
Same backend/tile-split as camarray_record.py; camera must be held still on a static scene."""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

COLS = 4
N = 25
outdir = Path("/home/garuda/euroc_live/static1")
import shutil
if outdir.exists():
    shutil.rmtree(outdir)
repo = Path("/home/garuda/garuda-sdk-deploy")
sys.path.insert(0, str(repo / "interfaces"))
sys.path.insert(0, str(repo / "hosts" / "pi5"))
from camarray_backend import CamArrayBackend

cam0d = outdir / "mav0" / "cam0" / "data"; cam1d = outdir / "mav0" / "cam1" / "data"
cam0d.mkdir(parents=True, exist_ok=True); cam1d.mkdir(parents=True, exist_ok=True)
st = {"last": 0.0, "n": 0}; ts = []
period = 1.0 / 4.0


def on_frame(meta, mv):
    now = time.monotonic()
    if now - st["last"] < period or st["n"] >= N:
        return
    st["last"] = now
    tw = meta.width // COLS
    arr = np.frombuffer(mv, dtype=np.uint8).reshape(meta.height, meta.width)
    name = str(meta.t_capture_ns)
    cv2.imwrite(str(cam0d / f"{name}.png"), np.ascontiguousarray(arr[:, 0:tw]))
    cv2.imwrite(str(cam1d / f"{name}.png"), np.ascontiguousarray(arr[:, tw:2 * tw]))
    ts.append(name); st["n"] += 1


backend = CamArrayBackend()
backend.configure(5120, 800, 10, pixel_format="GREY")
backend.start(on_frame)
t0 = time.monotonic()
while st["n"] < N and time.monotonic() - t0 < 40:
    time.sleep(0.25)
backend.stop()
(outdir / "times.txt").write_text("\n".join(ts) + "\n")
print(f"captured {len(ts)} static pairs -> {outdir}")
