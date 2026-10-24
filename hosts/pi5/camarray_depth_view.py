#!/usr/bin/env python3
"""Live stereo DEPTH view for the CamArray — rectify + SGBM + colorized depth, MJPEG.

Opens the CamArray via camarray_backend, loads the stereo calibration
(bench/camarray_stereo.yaml from calib/stereo_calibrate.py), rectifies each pair,
runs StereoSGBM on a downscaled rectified pair (passive-cooling budget), and serves
a browser view: rectified LEFT | colorized depth, with a center-pixel range readout.

    python3 hosts/pi5/camarray_depth_view.py [--yaml PATH] [--port 8093]

Closer = warmer colors; black = no match. The center-crosshair cm reading is metric
but only as good as the calibration's square_size (provisional until the
known-distance scale check). Open http://<pi-ip>:8093/
"""
from __future__ import annotations

import argparse
import os
import pathlib
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))                       # hosts/pi5
_base = pathlib.Path(__file__).resolve().parents[2]                                    # repo/deploy root
for _cand in (_base / "services" / "garuda-camera", _base / "garuda-camera"):          # repo | deploy layout
    if _cand.exists():
        sys.path.insert(0, str(_cand))
        break
from camarray_backend import CamArrayBackend  # noqa: E402
from stereo_rig import StereoRig  # noqa: E402

W, H, COLS = 5120, 800, 4
TILE_W = W // COLS
DW, DH = 640, 400            # SGBM compute size (downscaled rectified) — thermal budget
_COMPUTE_HZ = 2.0
_CAPTURE_VBLANK = "3600"     # slow the sensor (passive-cooled bench)

_jpg = {"buf": None}
_state = {"L": None, "R": None}
_lock = threading.Lock()


def on_frame(meta, mv):
    arr = np.frombuffer(mv, dtype=np.uint8).reshape(H, W)
    with _lock:
        _state["L"] = arr[:, 0:TILE_W].copy()
        _state["R"] = arr[:, TILE_W:2 * TILE_W].copy()


def depth_loop(rig):
    period = 1.0 / _COMPUTE_HZ
    s = DW / TILE_W                       # downscale factor
    focal_s = rig.focal_px * s            # focal at compute resolution
    cx, cy = DW // 2, DH // 2
    while True:
        with _lock:
            L, R = _state["L"], _state["R"]
        if L is None:
            time.sleep(0.1)
            continue
        rc1, rc2 = rig.rectify(L, R)                       # rect tile0 (cam1), tile1 (cam2)
        left_full, right_full = rig.order_for_matching(rc1, rc2)   # correct L/R for +disparity
        dl = cv2.resize(left_full, (DW, DH))               # reference frame (displayed left)
        dr = cv2.resize(right_full, (DW, DH))
        disp = rig.disparity(dl, dr)
        pos = disp > 0
        valid_pct = float(pos.mean() * 100.0)
        mx = float(np.percentile(disp[pos], 95)) if np.any(pos) else 1.0
        norm = np.clip(disp / max(mx, 1e-3) * 255.0, 0, 255).astype(np.uint8)
        color = cv2.applyColorMap(norm, cv2.COLORMAP_TURBO)
        color[~pos] = (0, 0, 0)           # no-match -> black
        d0 = disp[cy, cx]
        z_cm = (focal_s * rig.baseline_mm / d0) / 10.0 if d0 > 0 else 0.0
        left_bgr = cv2.cvtColor(dl, cv2.COLOR_GRAY2BGR)
        cv2.drawMarker(left_bgr, (cx, cy), (0, 255, 0), cv2.MARKER_CROSS, 22, 2)
        cv2.drawMarker(color, (cx, cy), (255, 255, 255), cv2.MARKER_CROSS, 22, 2)
        cv2.putText(color, (f"center: {z_cm:.1f} cm" if z_cm > 0 else "center: --"),
                    (10, DH - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.putText(color, f"valid: {valid_pct:.0f}%", (DW - 150, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(left_bgr, "rectified L", (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        div = np.full((DH, 4, 3), 40, np.uint8)
        combo = np.hstack([left_bgr, div, color])
        ok, jpg = cv2.imencode(".jpg", combo, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            _jpg["buf"] = jpg.tobytes()
        time.sleep(period)


_PAGE = (b"<!doctype html><html><head><title>CamArray depth</title>"
         b"<meta name=viewport content='width=device-width,initial-scale=1'></head>"
         b"<body style='margin:0;background:#111;color:#ccc;font-family:sans-serif;text-align:center'>"
         b"<p style='margin:8px'>CamArray stereo depth &mdash; rectified LEFT | depth "
         b"(warm=near, black=no match)</p>"
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
                    time.sleep(0.12)
            except (BrokenPipeError, ConnectionResetError):
                return
        else:
            self.send_response(404)
            self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default=None)
    ap.add_argument("--port", type=int, default=8093)
    args = ap.parse_args()
    yaml_path = pathlib.Path(args.yaml) if args.yaml else \
        pathlib.Path(__file__).resolve().parents[2] / "bench" / "camarray_stereo.yaml"
    rig = StereoRig(yaml_path)
    print(f"depth view: rig baseline={rig.baseline_mm:.1f}mm focal={rig.focal_px:.0f}px "
          f"rms={rig.rms_stereo:.2f}px  ->  http://0.0.0.0:{args.port}/")

    os.environ.setdefault("GARUDA_CAM_VBLANK", _CAPTURE_VBLANK)
    backend = CamArrayBackend()
    backend.configure(W, H, 30, pixel_format="GREY")
    backend.start(on_frame)
    threading.Thread(target=depth_loop, args=(rig,), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        srv.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        backend.stop()


if __name__ == "__main__":
    main()
