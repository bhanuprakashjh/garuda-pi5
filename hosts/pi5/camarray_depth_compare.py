#!/usr/bin/env python3
"""Side-by-side CPU-SGBM vs NPU-StereoNet depth for the CamArray — same frame, both engines.

BENCH-ONLY (CLAUDE.md rule 6): the NPU side runs the UNLICENSED StereoNet HEF (see
models/stereonet_h10.md). One process owns the camera (it's exclusive), rectifies once with our
calibration, then runs SGBM on the CPU and StereoNet on the Hailo on the same pair, and serves:

    [ rectified LEFT (reference) ]
    [ CPU SGBM depth | NPU StereoNet depth ]   each with density + latency

    python3 hosts/pi5/camarray_depth_compare.py [--swap] [--port 8095]
Open http://<pi-ip>:8095/  (warm = near; black = SGBM no-match).
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
SGBM_W, SGBM_H = 640, 400
PANE_W, PANE_H = 470, 294                 # each depth pane in the display
_VBLANK = "3600"

_jpg = {"buf": None}
_state = {"L": None, "R": None}
_lock = threading.Lock()


def on_frame(meta, mv):
    arr = np.frombuffer(mv, dtype=np.uint8).reshape(H, W)
    with _lock:
        _state["L"] = arr[:, 0:TILE_W].copy()
        _state["R"] = arr[:, TILE_W:2 * TILE_W].copy()


def _colorize(disp, valid_mask, pane_label, sub_label):
    """Percentile-stretch a disparity map to a TURBO pane at PANE_W x PANE_H."""
    if valid_mask.any():
        lo, hi = np.percentile(disp[valid_mask], 5), np.percentile(disp[valid_mask], 95)
    else:
        lo, hi = 0.0, 1.0
    norm = np.clip((disp - lo) / max(hi - lo, 1e-3) * 255.0, 0, 255).astype(np.uint8)
    color = cv2.applyColorMap(norm, cv2.COLORMAP_TURBO)
    color[~valid_mask] = (0, 0, 0)
    color = cv2.resize(color, (PANE_W, PANE_H))
    cv2.putText(color, pane_label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.putText(color, sub_label, (8, PANE_H - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
    return color


def depth_loop(rig, net):
    f_sgbm = rig.focal_px * (SGBM_W / TILE_W)
    f_npu = rig.focal_px * (IN_W / TILE_W)
    while True:
        with _lock:
            L, R = _state["L"], _state["R"]
        if L is None:
            time.sleep(0.05)
            continue
        rc1, rc2 = rig.rectify(L, R)
        left_full, right_full = rig.order_for_matching(rc1, rc2)

        # --- CPU SGBM ---
        dl = cv2.resize(left_full, (SGBM_W, SGBM_H))
        dr = cv2.resize(right_full, (SGBM_W, SGBM_H))
        t0 = time.monotonic()
        sgbm = rig.disparity(dl, dr)
        sgbm_ms = (time.monotonic() - t0) * 1000
        sgbm_ok = sgbm > 0
        zc_sgbm = (f_sgbm * rig.baseline_mm / sgbm[SGBM_H // 2, SGBM_W // 2]) / 10.0 \
            if sgbm[SGBM_H // 2, SGBM_W // 2] > 0 else 0.0

        # --- NPU StereoNet ---
        lm, rm = to_model_input(left_full), to_model_input(right_full)
        t0 = time.monotonic()
        npu = net.infer(lm, rm)
        npu_ms = (time.monotonic() - t0) * 1000
        npu_ok = np.ones_like(npu, dtype=bool)      # dense
        zc_npu = (f_npu * rig.baseline_mm / npu[IN_H // 2, IN_W // 2]) / 10.0 \
            if npu[IN_H // 2, IN_W // 2] > 0 else 0.0

        left_pane = _colorize(sgbm, sgbm_ok,
                              f"CPU SGBM   {1000/sgbm_ms:.1f} fps",
                              f"dense {sgbm_ok.mean()*100:.0f}%   center ~{zc_sgbm:.0f}cm")
        right_pane = _colorize(npu, npu_ok,
                               f"NPU StereoNet   {1000/npu_ms:.1f} fps",
                               f"dense 100%   center ~{zc_npu:.0f}cm (prov.)")
        for p in (left_pane, right_pane):
            cv2.drawMarker(p, (PANE_W // 2, PANE_H // 2), (255, 255, 255), cv2.MARKER_CROSS, 16, 1)
        vdiv = np.full((PANE_H, 4, 3), 40, np.uint8)
        depths = np.hstack([left_pane, vdiv, right_pane])

        ref = cv2.cvtColor(cv2.resize(left_full, (depths.shape[1], 190)), cv2.COLOR_GRAY2BGR)
        cv2.putText(ref, "rectified LEFT (same frame into both engines)", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
        hdiv = np.full((4, depths.shape[1], 3), 40, np.uint8)
        combo = np.vstack([ref, hdiv, depths])
        ok, jpg = cv2.imencode(".jpg", combo, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            _jpg["buf"] = jpg.tobytes()


_PAGE = (b"<!doctype html><html><head><title>CamArray depth: CPU vs NPU</title>"
         b"<meta name=viewport content='width=device-width,initial-scale=1'></head>"
         b"<body style='margin:0;background:#111;color:#ccc;font-family:sans-serif;text-align:center'>"
         b"<p style='margin:8px'>CamArray stereo depth &mdash; CPU SGBM vs NPU StereoNet "
         b"(same frame). Bench experiment.</p>"
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
                    time.sleep(0.06)
            except (BrokenPipeError, ConnectionResetError):
                return
        else:
            self.send_response(404)
            self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--swap", action="store_true")
    ap.add_argument("--port", type=int, default=8095)
    args = ap.parse_args()
    repo = pathlib.Path(__file__).resolve().parents[2]
    rig = StereoRig(repo / "bench" / "camarray_stereo.yaml")
    net = HailoStereoNet(repo / "models" / "stereonet_h10.hef", swap_inputs=args.swap)
    print(f"depth compare (CPU SGBM | NPU StereoNet) -> http://0.0.0.0:{args.port}/")

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
