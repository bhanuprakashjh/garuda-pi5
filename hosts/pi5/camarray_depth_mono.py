#!/usr/bin/env python3
"""Live monocular depth for the CamArray — fast_depth (MIT, NYU-indoor) on the Hailo-10H.

Unlike StereoNet this model is licence-clean (MIT) and indoor-trained, so it should read our
room better. Monocular: it takes ONE image (the left tile) and infers depth from learned cues —
no stereo, so it doesn't *measure* distance and the scale is relative (pin later if wanted).

    python3 hosts/pi5/camarray_depth_mono.py [--port 8096]
Open http://<pi-ip>:8096/  (warm = near).
"""
from __future__ import annotations

import argparse
import pathlib
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from camarray_backend import CamArrayBackend  # noqa: E402
from hailo_mono_depth import HailoMonoDepth, to_mono_input  # noqa: E402

W, H, COLS = 5120, 800, 4
TILE_W = W // COLS
DISP_W, DISP_H = 560, 350
_VBLANK = "3600"

_jpg = {"buf": None}
_state = {"L": None}
_label = {"txt": "mono depth"}          # set from the HEF name in main()
_lock = threading.Lock()


def on_frame(meta, mv):
    arr = np.frombuffer(mv, dtype=np.uint8).reshape(H, W)
    with _lock:
        _state["L"] = arr[:, 0:TILE_W].copy()      # tile0 (mono: single view is fine)


def depth_loop(net):
    ema = None
    while True:
        with _lock:
            L = _state["L"]
        if L is None:
            time.sleep(0.05)
            continue
        inp = to_mono_input(L, net.in_w, net.in_h)
        t0 = time.monotonic()
        depth = net.infer(inp)                       # (224,224) float, learned depth
        dt = time.monotonic() - t0
        ema = dt if ema is None else 0.85 * ema + 0.15 * dt
        fps = 1.0 / ema if ema else 0.0
        lo, hi = np.percentile(depth, 5), np.percentile(depth, 95)
        norm = np.clip((depth - lo) / max(hi - lo, 1e-3) * 255.0, 0, 255).astype(np.uint8)
        # near = warm: fast_depth outputs larger values for farther -> invert the colormap input
        color = cv2.applyColorMap(255 - norm, cv2.COLORMAP_TURBO)
        color = cv2.resize(color, (DISP_W, DISP_H), interpolation=cv2.INTER_NEAREST)
        left = cv2.cvtColor(cv2.resize(L, (DISP_W, DISP_H)), cv2.COLOR_GRAY2BGR)
        cx, cy = net.in_w // 2, net.in_h // 2
        cv2.putText(left, "LEFT tile (mono input)", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
        cv2.putText(color, f"{_label['txt']}  {fps:.0f} fps", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        cv2.putText(color, f"center raw={depth[cy, cx]:.0f} (relative)", (8, DISP_H - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        cv2.drawMarker(color, (DISP_W // 2, DISP_H // 2), (255, 255, 255), cv2.MARKER_CROSS, 16, 1)
        div = np.full((DISP_H, 4, 3), 40, np.uint8)
        combo = np.hstack([left, div, color])
        ok, jpg = cv2.imencode(".jpg", combo, [cv2.IMWRITE_JPEG_QUALITY, 78])
        if ok:
            _jpg["buf"] = jpg.tobytes()


_PAGE = (b"<!doctype html><html><head><title>CamArray mono depth</title>"
         b"<meta name=viewport content='width=device-width,initial-scale=1'></head>"
         b"<body style='margin:0;background:#111;color:#ccc;font-family:sans-serif;text-align:center'>"
         b"<p style='margin:8px'>CamArray monocular depth &mdash; fast_depth (MIT, NYU-indoor) on "
         b"the Hailo-10H. LEFT image / depth (warm=near).</p>"
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
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                return
        else:
            self.send_response(404)
            self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8096)
    ap.add_argument("--hef", default=None)
    args = ap.parse_args()
    repo = pathlib.Path(__file__).resolve().parents[2]
    hef = pathlib.Path(args.hef) if args.hef else repo / "models" / "fast_depth_h10.hef"
    net = HailoMonoDepth(hef)
    _label["txt"] = hef.stem.replace("_h10", "")
    print(f"mono depth: {_label['txt']} on Hailo-10H  in={net.in_w}x{net.in_h}  "
          f"->  http://0.0.0.0:{args.port}/")

    import os
    os.environ.setdefault("GARUDA_CAM_VBLANK", _VBLANK)
    backend = CamArrayBackend()
    backend.configure(W, H, 30, pixel_format="GREY")
    backend.start(on_frame)
    threading.Thread(target=depth_loop, args=(net,), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        srv.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        backend.stop()
        net.close()


if __name__ == "__main__":
    main()
