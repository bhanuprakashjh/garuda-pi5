#!/usr/bin/env python3
"""camarray_record — record a live CamArray stereo clip in EuRoC layout for ORB-SLAM3.

Captures the stereo pair (tiles 0 & 1 of the combined OV9281 frame) to
  <outdir>/mav0/cam0/data/<t_ns>.png   (tile0)
  <outdir>/mav0/cam1/data/<t_ns>.png   (tile1)
  <outdir>/times.txt                   (one t_ns per line — the ORB-SLAM3 timestamps file)
so stereo_euroc can replay it. MOVE THE CAMERA during the recording — SLAM needs motion to map.

    python3 hosts/pi5/camarray_record.py <outdir> [seconds] [fps]
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

COLS = 4   # OV9281 quad: 4 tiles side-by-side in the combined frame


def main():
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "euroc_live/seq1")
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    fps = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0

    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "interfaces"))
    sys.path.insert(0, str(repo / "hosts" / "pi5"))
    from camarray_backend import CamArrayBackend

    cam0d = outdir / "mav0" / "cam0" / "data"
    cam1d = outdir / "mav0" / "cam1" / "data"
    cam0d.mkdir(parents=True, exist_ok=True)
    cam1d.mkdir(parents=True, exist_ok=True)

    ts, st = [], {"last": 0.0, "n": 0}
    lock = threading.Lock()
    period = 1.0 / fps

    def on_frame(meta, mv):
        now = time.monotonic()
        if now - st["last"] < period:
            return
        st["last"] = now
        tw = meta.width // COLS
        arr = np.frombuffer(mv, dtype=np.uint8).reshape(meta.height, meta.width)
        name = str(meta.t_capture_ns)
        cv2.imwrite(str(cam0d / f"{name}.png"), np.ascontiguousarray(arr[:, 0:tw]))
        cv2.imwrite(str(cam1d / f"{name}.png"), np.ascontiguousarray(arr[:, tw:2 * tw]))
        with lock:
            ts.append(name)
        st["n"] += 1

    backend = CamArrayBackend()
    backend.configure(5120, 800, int(fps), pixel_format="GREY")   # runs camarray_setup.sh, sets frame size
    backend.start(on_frame)
    print(f"RECORDING {seconds:.0f}s @ ~{fps:.0f}fps -> {outdir}   *** MOVE THE CAMERA (slow, smooth) ***")
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < seconds:
            time.sleep(0.25)
            print(f"\r  captured {st['n']} stereo pairs...", end="", flush=True)
    finally:
        backend.stop()
    (outdir / "times.txt").write_text("\n".join(ts) + "\n")
    print(f"\ndone: {len(ts)} stereo pairs -> {outdir}  (times.txt written)")


if __name__ == "__main__":
    main()
