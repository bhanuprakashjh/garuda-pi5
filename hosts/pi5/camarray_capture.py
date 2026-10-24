#!/usr/bin/env python3
"""Stereo-calibration capture tool for the CamArray — browser-driven (headless Pi).

Opens the CamArray through hosts/pi5/camarray_backend.py, splits tiles 0/1 into
L/R, and serves a browser page showing the live pair with the checkerboard corners
overlaid, so you can SEE when BOTH cameras have a full detection before you keep a
shot. Saves raw 1280x800 PNG pairs to calib/captures/ for calib/stereo_calibrate.py.

    python3 hosts/pi5/camarray_capture.py --cols 11 --rows 8 [--out DIR] [--port 8092]

--cols/--rows are INNER corners (11x8 for the A3 close-range board = 12x9 squares).
Open http://<pi-ip>:8092/ , move the board around, press "Capture" (or C) when both
frames show a green corner grid. Aim for 30-50 pairs: near/far, all edges, tilted.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))       # hosts/pi5
from camarray_backend import CamArrayBackend  # noqa: E402

W, H, COLS = 5120, 800, 4
TILE_W = W // COLS                       # 1280 per sensor
PREVIEW_W, PREVIEW_H = 640, 400          # detection + display size (half-res: fast, cool)
_SB_FLAGS = cv2.CALIB_CB_NORMALIZE_IMAGE
_DETECT_HZ = 2.0                         # live feedback only; saved pairs stay full-res raw
# Passive-cooled bench: cap the sensor frame rate (bigger vertical blanking) so raw
# ingest doesn't peg a core. ~10 fps is plenty to position a calibration board.
_CAPTURE_VBLANK = "3600"
_HOLD_SEC = 1.0                           # latch green ~1s after a detection (2 Hz detect flickers)

_state = {"L": None, "R": None, "cL": None, "cR": None,
          "tL": 0.0, "tR": 0.0, "saved_t": 0.0, "count": 0}
_lock = threading.Lock()
_jpg = {"buf": None}          # single shared encoded preview (encode ONCE, not per client)


def _find(gray, pattern):
    try:
        ok, corners = cv2.findChessboardCornersSB(gray, pattern, _SB_FLAGS)
    except cv2.error:
        ok, corners = False, None
    return bool(ok), corners


def _next_index(outdir: pathlib.Path) -> int:
    n = -1
    for p in outdir.glob("pair*_L.png"):
        m = re.search(r"pair(\d+)_L", p.name)
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


class Capturer:
    def __init__(self, pattern, outdir: pathlib.Path):
        self.pattern = pattern
        self.outdir = outdir
        self.outdir.mkdir(parents=True, exist_ok=True)
        with _lock:
            _state["count"] = _next_index(self.outdir)
        self._run = True

    def on_frame(self, meta, mv):
        arr = np.frombuffer(mv, dtype=np.uint8).reshape(H, W)
        with _lock:
            _state["L"] = arr[:, 0:TILE_W].copy()
            _state["R"] = arr[:, TILE_W:2 * TILE_W].copy()

    def detect_loop(self):
        # Detect at FULL resolution (half-res missed the board), but only a couple
        # times per second — it's the tight full-res LOOP that overheats the Pi, not
        # a single detection. Corners get scaled into PREVIEW space for _render.
        period = 1.0 / _DETECT_HZ
        scale = np.array([PREVIEW_W / TILE_W, PREVIEW_H / H], np.float32)
        while self._run:
            with _lock:
                L, R = _state["L"], _state["R"]
            if L is None:
                time.sleep(0.1)
                continue
            okL, cL = _find(L, self.pattern)
            okR, cR = _find(R, self.pattern)
            now = time.monotonic()
            with _lock:
                if okL and cL is not None:
                    _state["cL"], _state["tL"] = (cL * scale).astype(np.float32), now
                if okR and cR is not None:
                    _state["cR"], _state["tR"] = (cR * scale).astype(np.float32), now
            time.sleep(period)

    def save(self):
        with _lock:
            L, R, n = _state["L"], _state["R"], _state["count"]
        if L is None:
            return None
        cv2.imwrite(str(self.outdir / f"pair{n:02d}_L.png"), L)
        cv2.imwrite(str(self.outdir / f"pair{n:02d}_R.png"), R)
        with _lock:
            _state["count"] = n + 1
            _state["saved_t"] = time.monotonic()
        return n

    def stop(self):
        self._run = False


def _both_latched():
    now = time.monotonic()
    with _lock:
        return (now - _state["tL"] < _HOLD_SEC) and (now - _state["tR"] < _HOLD_SEC)


def _render(pattern):
    now = time.monotonic()
    with _lock:
        L, R, cL, cR = _state["L"], _state["R"], _state["cL"], _state["cR"]
        tL, tR, saved_t, n = _state["tL"], _state["tR"], _state["saved_t"], _state["count"]
    if L is None:
        blank = np.zeros((400, 1284, 3), np.uint8)
        cv2.putText(blank, "waiting for frames...", (20, 200),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (200, 200, 200), 2)
        return blank
    holdL = (now - tL) < _HOLD_SEC        # latched green — 2 Hz detection otherwise flickers
    holdR = (now - tR) < _HOLD_SEC
    # Resize first, then draw the (PREVIEW-space) corners on the display image.
    lb = cv2.cvtColor(cv2.resize(L, (PREVIEW_W, PREVIEW_H)), cv2.COLOR_GRAY2BGR)
    rb = cv2.cvtColor(cv2.resize(R, (PREVIEW_W, PREVIEW_H)), cv2.COLOR_GRAY2BGR)
    if holdL and cL is not None:
        cv2.drawChessboardCorners(lb, pattern, cL, True)
    if holdR and cR is not None:
        cv2.drawChessboardCorners(rb, pattern, cR, True)
    for im, ok in ((lb, holdL), (rb, holdR)):
        cv2.rectangle(im, (2, 2), (PREVIEW_W - 3, PREVIEW_H - 3),
                      (0, 210, 0) if ok else (40, 40, 210), 3)
    both = holdL and holdR
    div = np.full((400, 4, 3), 40, np.uint8)
    combo = np.hstack([lb, div, rb])
    msg = f"pairs: {n}   " + ("BOTH DETECTED - press C" if both else "align board in BOTH frames")
    cv2.putText(combo, msg, (10, 388), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (0, 230, 0) if both else (60, 60, 230), 2)
    if (now - saved_t) < 0.8:             # brief "saved" confirmation flash
        cv2.putText(combo, "SAVED", (PREVIEW_W - 118, 388), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (0, 255, 255), 2)
    return combo


def encode_loop(pattern):
    """Encode the preview ONCE for all viewers (~9 fps). Per-connection encoding
    multiplied CPU per open tab and could re-throttle the Pi."""
    while True:
        ok, jpg = cv2.imencode(".jpg", _render(pattern), [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            _jpg["buf"] = jpg.tobytes()
        time.sleep(0.15)


_PAGE = b"""<!doctype html><html><head><title>CamArray stereo calibration capture</title>
<meta name=viewport content='width=device-width,initial-scale=1'>
<style>body{margin:0;background:#111;color:#ddd;font-family:sans-serif;text-align:center}
button{font-size:20px;padding:12px 28px;margin:12px;border:0;border-radius:8px;background:#2a7;color:#fff}
#info{font-size:18px}</style></head><body>
<p style='margin:8px'>Arducam CamArray &mdash; stereo calibration capture (LEFT | RIGHT)</p>
<img src='/stream' style='max-width:100%;height:auto'>
<div><button onclick='cap()'>Capture &nbsp;(or press C)</button><span id=info>&nbsp;</span></div>
<script>
async function cap(){let r=await fetch('/capture',{method:'POST'});let j=await r.json();
 document.getElementById('info').textContent=' saved pair #'+(j.saved);}
document.addEventListener('keydown',e=>{if(e.key=='c'||e.key=='C')cap();});
setInterval(async()=>{let r=await fetch('/status');let j=await r.json();
 document.getElementById('info').textContent=' pairs saved: '+j.count+(j.both?'   OK BOTH DETECTED':'   ... align board');},400);
</script></body></html>"""


def make_handler(cap: Capturer, pattern):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(_PAGE)
            elif self.path == "/status":
                both = _both_latched()
                with _lock:
                    count = _state["count"]
                body = json.dumps({"count": count, "both": both}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        b = _jpg["buf"]
                        if b:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                             b"Content-Length: " + str(len(b)).encode() + b"\r\n\r\n")
                            self.wfile.write(b)
                            self.wfile.write(b"\r\n")
                        time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError):
                    return
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/capture":
                saved = cap.save()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"saved": saved}).encode())
            else:
                self.send_response(404)
                self.end_headers()
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cols", type=int, default=11, help="inner corners across (12 squares -> 11)")
    ap.add_argument("--rows", type=int, default=8, help="inner corners down (9 squares -> 8)")
    ap.add_argument("--out", default=None, help="capture dir (default: repo calib/captures)")
    ap.add_argument("--port", type=int, default=8092)
    args = ap.parse_args()
    pattern = (args.cols, args.rows)
    outdir = pathlib.Path(args.out) if args.out else \
        pathlib.Path(__file__).resolve().parents[2] / "calib" / "captures"

    os.environ.setdefault("GARUDA_CAM_VBLANK", _CAPTURE_VBLANK)  # slow the sensor (passive cooling)
    backend = CamArrayBackend()
    backend.configure(W, H, 30, pixel_format="GREY")
    cap = Capturer(pattern, outdir)
    backend.start(cap.on_frame)
    threading.Thread(target=cap.detect_loop, daemon=True).start()
    threading.Thread(target=encode_loop, args=(pattern,), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(cap, pattern))
    # Clean shutdown on `kill` (SIGTERM) too, so the v4l2-ctl child is terminated
    # via backend.stop() instead of being orphaned holding /dev/video0.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"capture: http://0.0.0.0:{args.port}/  pattern={pattern} -> {outdir}")
    try:
        srv.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        cap.stop()
        backend.stop()


if __name__ == "__main__":
    main()
