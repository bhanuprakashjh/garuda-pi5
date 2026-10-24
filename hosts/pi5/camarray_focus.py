#!/usr/bin/env python3
"""camarray_focus — live focus-assist for the OV9281 CamArray stereo pair, served as MJPEG.

The OV9281 has NO software focus control (manual M12 lens; verified via v4l2 — only exposure/gain).
This shows each camera + a zoomed center crop + a SHARPNESS score (variance of Laplacian, peak-held).
Rotate each lens barrel until its number peaks; aim for both LEFT and RIGHT sharp AND matched — the
live-SLAM failure was traced to the two lenses being focus-mismatched (bench/benchlog.md 2026-08-03).

    python3 hosts/pi5/camarray_focus.py [port]        # default 8092;  http://<pi-ip>:8092/
Stop garuda-depth first (it holds the camera):  sudo systemctl stop garuda-depth
"""
from __future__ import annotations

import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from camarray_backend import CamArrayBackend  # noqa: E402

W, H, COLS = 5120, 800, 4
TILE_W = W // COLS
_MIN_DT = 1.0 / 15.0
CROP = 300                      # center-crop side used for sharpness + zoom

_latest = {"jpg": None}
_lock = threading.Lock()
_last = [0.0]
_peak = {"L": 1.0, "R": 1.0}


def _panel(tile, label, key):
    h, w = tile.shape
    cy, cx = h // 2, w // 2
    crop = tile[cy - CROP // 2:cy + CROP // 2, cx - CROP // 2:cx + CROP // 2]
    s = float(cv2.Laplacian(crop, cv2.CV_64F).var())
    _peak[key] = max(_peak[key] * 0.997, s)                 # slow-decay peak hold
    good = s > 0.9 * _peak[key]
    color = (90, 240, 90) if good else (0, 170, 255)

    full = cv2.cvtColor(cv2.resize(tile, (480, 300)), cv2.COLOR_GRAY2BGR)
    cv2.rectangle(full,
                  (int(480 * (cx - CROP // 2) / w), int(300 * (cy - CROP // 2) / h)),
                  (int(480 * (cx + CROP // 2) / w), int(300 * (cy + CROP // 2) / h)),
                  (0, 255, 255), 1)
    cv2.putText(full, label, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    # bottom row must match full's width (480): zoom (430) + bar (50)
    zoom = cv2.cvtColor(cv2.resize(crop, (430, 300), interpolation=cv2.INTER_NEAREST),
                        cv2.COLOR_GRAY2BGR)
    cv2.putText(zoom, f"{s:5.0f}", (8, 46), cv2.FONT_HERSHEY_SIMPLEX, 1.3, color, 3)
    cv2.putText(zoom, f"peak {_peak[key]:5.0f}", (8, 288), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (210, 210, 210), 1)

    bar = np.full((300, 50, 3), 25, np.uint8)
    frac = min(1.0, s / _peak[key]) if _peak[key] > 0 else 0.0
    cv2.rectangle(bar, (8, int(300 * (1 - frac))), (44, 298), color, -1)
    return np.vstack([full, np.hstack([zoom, bar])])


def on_frame(meta, mv):
    now = time.monotonic()
    if now - _last[0] < _MIN_DT:
        return
    _last[0] = now
    arr = np.frombuffer(mv, dtype=np.uint8).reshape(H, W)
    L = _panel(arr[:, 0:TILE_W], "LEFT (tile0)", "L")
    R = _panel(arr[:, TILE_W:2 * TILE_W], "RIGHT (tile1)", "R")
    div = np.full((L.shape[0], 6, 3), 40, np.uint8)
    combo = np.hstack([L, div, R])
    ok, jpg = cv2.imencode(".jpg", combo, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if ok:
        with _lock:
            _latest["jpg"] = jpg.tobytes()


_PAGE = (b"<!doctype html><html><head><title>CamArray focus assist</title>"
         b"<meta name=viewport content='width=device-width,initial-scale=1'></head>"
         b"<body style='margin:0;background:#0b0b0b;color:#ccc;font-family:sans-serif;text-align:center'>"
         b"<p style='margin:8px'>OV9281 focus assist &mdash; rotate each lens until its number PEAKS; "
         b"match LEFT &amp; RIGHT. Aim the center box at a textured, ~1&ndash;2 m target.</p>"
         b"<img src='/stream' style='max-width:100%;height:auto'></body></html>")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
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
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8092
    backend = CamArrayBackend()
    backend.configure(W, H, 30, pixel_format="GREY")
    backend.start(on_frame)
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"CamArray focus assist on http://0.0.0.0:{port}/  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    finally:
        backend.stop()


if __name__ == "__main__":
    main()
