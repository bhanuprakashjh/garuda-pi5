#!/usr/bin/env python3
"""Live side-by-side stereo preview of the Arducam CamArray, served as MJPEG.

Bench VIEWING tool — a downscaled software-JPEG preview, deliberately NOT a
benchmark (Pi 5 has no HW video encoder; CLAUDE.md forbids software-encoding
during benchmarks — this is a look-at-it tool, not a measurement). Opens the
CamArray through hosts/pi5/camarray_backend.py, splits the combined 5120x800
frame into the connected tiles (0,1 = the stereo pair), stacks them L|R, and
serves an MJPEG stream so you can watch both cameras from a browser:

    http://<pi-ip>:8091/

Usage:  python3 hosts/pi5/camarray_view.py [port]
"""
from __future__ import annotations

import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))                 # hosts/pi5
from camarray_backend import CamArrayBackend  # noqa: E402

W, H, COLS = 5120, 800, 4
TILE_W = W // COLS                       # 1280 per sensor
PREVIEW_W, PREVIEW_H = 640, 400          # per-eye downscale for the preview
_MIN_DT = 1.0 / 15.0                     # cap encode/preview at ~15 fps (light CPU)

_latest = {"jpg": None}
_lock = threading.Lock()
_last = [0.0]


def on_frame(meta, mv):
    now = time.monotonic()
    if now - _last[0] < _MIN_DT:          # throttle: don't peg a core encoding
        return
    _last[0] = now
    arr = np.frombuffer(mv, dtype=np.uint8).reshape(H, W)
    left = cv2.resize(arr[:, 0:TILE_W], (PREVIEW_W, PREVIEW_H))
    right = cv2.resize(arr[:, TILE_W:2 * TILE_W], (PREVIEW_W, PREVIEW_H))
    lb = cv2.cvtColor(left, cv2.COLOR_GRAY2BGR)
    rb = cv2.cvtColor(right, cv2.COLOR_GRAY2BGR)
    cv2.putText(lb, "LEFT  (tile 0)", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    cv2.putText(rb, "RIGHT (tile 1)", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    div = np.full((PREVIEW_H, 4, 3), 40, np.uint8)
    combo = np.hstack([lb, div, rb])
    ok, jpg = cv2.imencode(".jpg", combo, [cv2.IMWRITE_JPEG_QUALITY, 72])
    if ok:
        with _lock:
            _latest["jpg"] = jpg.tobytes()


_PAGE = (b"<!doctype html><html><head><title>CamArray stereo</title>"
         b"<meta name=viewport content='width=device-width,initial-scale=1'></head>"
         b"<body style='margin:0;background:#111;color:#ccc;font-family:sans-serif;text-align:center'>"
         b"<p style='margin:8px'>Arducam CamArray &mdash; live stereo pair (OV9281)</p>"
         b"<img src='/stream' style='max-width:100%;height:auto'></body></html>")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_PAGE)
            return
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    with _lock:
                        jpg = _latest["jpg"]
                    if jpg:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n")
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                    time.sleep(_MIN_DT)
            except (BrokenPipeError, ConnectionResetError):
                return
        self.send_response(404)
        self.end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8091
    backend = CamArrayBackend()
    backend.configure(W, H, 30, pixel_format="GREY")
    backend.start(on_frame)
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"CamArray stereo preview serving on http://0.0.0.0:{port}/  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    finally:
        backend.stop()


if __name__ == "__main__":
    main()
