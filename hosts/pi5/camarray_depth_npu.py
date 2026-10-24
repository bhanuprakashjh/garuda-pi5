#!/usr/bin/env python3
"""Live NPU stereo depth for the CamArray — StereoNet on the Hailo-10H, MJPEG.

BENCH-ONLY (CLAUDE.md rule 6): runs the UNLICENSED StereoNet via Hailo's pre-compiled HEF as an
experiment to prove NPU depth + benchmark it vs CPU SGBM. NOT product-cleared.

Pipeline: CamArray -> split -> rectify (CPU, our calibration) -> StereoNet disparity (NPU) ->
metric depth (Z=f*B/d) -> colorized view. The heavy matching runs on the Hailo; the CPU only
rectifies/encodes, so this should be far lighter on CPU than the SGBM view.

    python3 hosts/pi5/camarray_depth_npu.py [--swap] [--port 8094]
--swap flips which HEF input is the left reference (use if the depth looks inverted).
Open http://<pi-ip>:8094/  (warm=near). Absolute scale is provisional until a known-distance pin.
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))                       # hosts/pi5
_base = pathlib.Path(__file__).resolve().parents[2]
for _cand in (_base / "services" / "garuda-camera", _base / "garuda-camera"):
    if _cand.exists():
        sys.path.insert(0, str(_cand))
        break
from camarray_backend import CamArrayBackend  # noqa: E402
from stereo_rig import StereoRig  # noqa: E402
from hailo_stereo import HailoStereoNet, to_model_input, IN_W, IN_H  # noqa: E402

W, H, COLS = 5120, 800, 4
TILE_W = W // COLS
DISP_W = 920
DISP_H = round(IN_H * DISP_W / IN_W)     # keep the 1232x368 aspect
_VBLANK = "3600"

_jpg = {"buf": None}
_state = {"L": None, "R": None, "fps": 0.0}
_lock = threading.Lock()


def on_frame(meta, mv):
    arr = np.frombuffer(mv, dtype=np.uint8).reshape(H, W)
    with _lock:
        _state["L"] = arr[:, 0:TILE_W].copy()
        _state["R"] = arr[:, TILE_W:2 * TILE_W].copy()


def depth_loop(rig, net):
    focal_s = rig.focal_px * (IN_W / TILE_W)     # rectified focal at the 1232-wide model input
    cx, cy = IN_W // 2, IN_H // 2
    ema = None
    while True:
        with _lock:
            L, R = _state["L"], _state["R"]
        if L is None:
            time.sleep(0.05)
            continue
        rc1, rc2 = rig.rectify(L, R)
        left_full, right_full = rig.order_for_matching(rc1, rc2)
        lm, rm = to_model_input(left_full), to_model_input(right_full)
        t0 = time.monotonic()
        disp = net.infer(lm, rm)                 # (368,1232) float, StereoNet raw disparity
        dt = time.monotonic() - t0
        ema = dt if ema is None else 0.8 * ema + 0.2 * dt
        with _lock:
            _state["fps"] = 1.0 / ema if ema else 0.0
        # metric depth at center (provisional: treat raw disparity as px at 1232-wide)
        d0 = float(disp[cy, cx])
        z_cm = (focal_s * rig.baseline_mm / d0) / 10.0 if d0 > 0 else 0.0
        # colorize: stretch to 5-95 percentile for contrast; higher disparity = nearer = warm
        lo, hi = np.percentile(disp, 5), np.percentile(disp, 95)
        norm = np.clip((disp - lo) / max(hi - lo, 1e-3) * 255.0, 0, 255).astype(np.uint8)
        color = cv2.resize(cv2.applyColorMap(norm, cv2.COLORMAP_TURBO), (DISP_W, DISP_H))
        left_bgr = cv2.resize(cv2.cvtColor(lm, cv2.COLOR_RGB2BGR), (DISP_W, DISP_H))
        cv2.drawMarker(color, (DISP_W // 2, DISP_H // 2), (255, 255, 255), cv2.MARKER_CROSS, 18, 2)
        cv2.putText(left_bgr, "rectified L (NPU StereoNet)", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
        with _lock:
            fps = _state["fps"]
        cv2.putText(color, f"NPU {fps:.1f} fps", (DISP_W - 150, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        cv2.putText(color, (f"center ~{z_cm:.0f} cm (provisional scale)" if z_cm > 0 else "center: --"),
                    (8, DISP_H - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        div = np.full((4, DISP_W, 3), 40, np.uint8)
        combo = np.vstack([left_bgr, div, color])
        ok, jpg = cv2.imencode(".jpg", combo, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            _jpg["buf"] = jpg.tobytes()


_PAGE = (b"<!doctype html><html><head><title>CamArray NPU depth</title>"
         b"<meta name=viewport content='width=device-width,initial-scale=1'></head>"
         b"<body style='margin:0;background:#111;color:#ccc;font-family:sans-serif;text-align:center'>"
         b"<p style='margin:8px'>CamArray stereo depth on the Hailo-10H NPU (StereoNet) &mdash; "
         b"rectified LEFT / depth (warm=near). Bench experiment.</p>"
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
    ap.add_argument("--swap", action="store_true", help="flip which HEF input is the left reference")
    ap.add_argument("--port", type=int, default=8094)
    ap.add_argument("--yaml", default=None)
    args = ap.parse_args()
    repo = pathlib.Path(__file__).resolve().parents[2]
    yaml_path = pathlib.Path(args.yaml) if args.yaml else repo / "bench" / "camarray_stereo.yaml"
    rig = StereoRig(yaml_path)
    net = HailoStereoNet(repo / "models" / "stereonet_h10.hef", swap_inputs=args.swap)
    print(f"NPU depth: StereoNet on Hailo-10H  baseline={rig.baseline_mm:.1f}mm  "
          f"swap={args.swap}  ->  http://0.0.0.0:{args.port}/")

    import os
    os.environ.setdefault("GARUDA_CAM_VBLANK", _VBLANK)
    backend = CamArrayBackend()
    backend.configure(W, H, 30, pixel_format="GREY")
    backend.start(on_frame)
    threading.Thread(target=depth_loop, args=(rig, net), daemon=True).start()
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
