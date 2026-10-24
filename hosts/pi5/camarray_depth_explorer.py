#!/usr/bin/env python3
"""Unified live depth EXPLORER — switch engines from the browser.

One process owns the camera; picking an engine (SGBM / StereoNet / fast_depth / scdepthv3) from
the web UI reconfigures on the fly (the NPU model is (re)loaded on switch, ~1s). SGBM is metric
(real cm); StereoNet is provisional metric; the mono nets are relative (no true scale).

BENCH: includes EVAL-ONLY models (StereoNet unlicensed, scdepthv3 GPL-3.0) — see models/LICENCES.md;
these never ship. Open http://<pi-ip>:8097/

    python3 hosts/pi5/camarray_depth_explorer.py [--port 8097]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

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
from hailo_mono_depth import HailoMonoDepth, to_mono_input  # noqa: E402
from target_range import range_target, select_target  # noqa: E402
from roi_disparity import roi_disparity  # noqa: E402
from hailo_detect import HailoDetector  # noqa: E402

W, H, COLS = 5120, 800, 4
TILE_W = W // COLS
SGBM_W, SGBM_H = 640, 400
DISP_W, DISP_H = 760, 460

# Frame-rate cap for the compute loop. A distance/depth readout does not need a fast video;
# capping stops the per-frame CPU wrapper (rectify + colorize + JPEG-encode, run on EVERY
# engine incl. NPU) from spinning flat-out and pegging the passively-cooled Pi. Overridable
# via GARUDA_DEPTH_FPS for bench experiments; 6 fps keeps CPU sane while staying responsive.
DEPTH_FPS_CAP = max(1.0, float(os.environ.get("GARUDA_DEPTH_FPS", "6")))

# name -> (button label, kind, hef filename)
MODELS = [
    ("sgbm",       "SGBM  (CPU · metric)",     "sgbm",       None),
    ("pointcloud", "Point cloud  (CPU · 3D)",  "pointcloud", None),
    ("stereonet",  "StereoNet  (NPU)",         "stereo_npu", "stereonet_h10.hef"),
    ("fast_depth", "fast_depth  (NPU · MIT)",  "mono_npu",   "fast_depth_h10.hef"),
    ("scdepthv3",  "scdepthv3  (NPU · GPL)",   "mono_npu",   "scdepthv3_h10.hef"),
]
_CPU_ENGINES = {"sgbm", "pointcloud"}     # no Hailo -> switch in-process (no restart)
KIND = {n: k for n, _, k, _ in MODELS}
HEF = {n: h for n, _, _, h in MODELS}

_jpg = {"buf": None}
_state = {"L": None, "R": None, "seq": 0}   # seq bumps per delivered frame (freshness watchdog)
_lock = threading.Lock()
_cur = {"name": "sgbm", "net": None, "loading": False, "fps": 0.0}
_clock = threading.Lock()
_switch_lock = threading.Lock()
_mono_scale = {}          # mono model name -> (mode, k): SGBM-referenced raw->cm mapping
_viewers = {"n": 0}       # active /stream clients — compute only while >0 (idle = no CPU/heat)

# --- Stereo target range (Option B): detect yolov8m on the stereo-LEFT, range the locked class ---
_range = {"class": None, "result": None, "t": 0.0, "dets": []}   # active class -> latest RangeResult
_range_lock = threading.Lock()
_range_jpg = {"buf": None}          # left-image + boxes + Z overlay, served at /range_stream
_range_viewers = {"n": 0}           # draw the overlay only while /range_stream has a viewer
RANGE_FPS = max(1.0, float(os.environ.get("GARUDA_RANGE_FPS", "6")))   # cap detect+range loop
RANGE_STALE_MS = 1000     # /api/target_range reports invalid past this age (camera stalled -> not live)


def on_frame(meta, mv):
    # idle only when neither depth nor range (locked or previewing) needs frames. The dict reads are
    # lock-free: GIL-atomic, and only ever flip idle<->active a frame early/late, which self-corrects.
    if _viewers["n"] == 0 and _range["class"] is None and _range_viewers["n"] == 0:
        return
    arr = np.frombuffer(mv, dtype=np.uint8).reshape(H, W)
    with _lock:
        _state["L"] = arr[:, 0:TILE_W].copy()
        _state["R"] = arr[:, TILE_W:2 * TILE_W].copy()
        _state["seq"] += 1


_vdevice = {"vd": None}   # ONE shared Hailo VDevice for all engines (recreating it corrupts state)


def _shared_vdevice():
    if _vdevice["vd"] is None:
        from hailo_platform import VDevice
        _vdevice["vd"] = VDevice(VDevice.create_params())
    return _vdevice["vd"]


_reexec_cfg = {"backend": None, "port": 8097}
_MODEL_FILE = pathlib.Path.home() / ".garuda-depth-model"   # last-picked engine (survives restart)


def _reexec(name):
    """Switch engine by recording the pick and EXITING; systemd (Restart=always) starts a fresh
    process which reads the file. Must be a full process death, not os.execv — execv keeps the
    wedged Hailo device fd, so the new image inherits the stuck NPU (async-job timeout)."""
    import os
    try:
        _MODEL_FILE.write_text(name)
    except Exception:
        pass
    b = _reexec_cfg["backend"]
    if b is not None:
        try:
            b.stop()                 # free the camera child; systemd cgroup-cleans any straggler
        except Exception:
            pass
    os._exit(0)                      # full death -> Hailo resets -> systemd restarts on the new model


def switch(name, models_dir):
    """(Re)load the selected engine. Runs in a thread so the HTTP call returns fast."""
    with _switch_lock:
        with _clock:
            _cur["loading"] = True
            old = _cur["net"]
        if old is not None:
            old.close()
        net = None
        try:
            if KIND[name] == "stereo_npu":
                net = HailoStereoNet(models_dir / HEF[name], vdevice=_shared_vdevice())
            elif KIND[name] == "mono_npu":
                net = HailoMonoDepth(models_dir / HEF[name], vdevice=_shared_vdevice())
        except Exception as e:
            with _clock:
                _cur["name"], _cur["net"], _cur["loading"] = "sgbm", None, False
            print(f"switch to {name} failed: {e}")
            return
        with _clock:
            _cur["name"], _cur["net"], _cur["loading"] = name, net, False


def _calibrate_mono(rig, net, name):
    """Auto-scale a monocular net to metric using SGBM as the reference on one frame:
    fit raw->cm (linear if raw grows with depth, inverse if it grows toward the camera)."""
    with _lock:
        L, R = _state["L"], _state["R"]
    if L is None:
        return
    rc1, rc2 = rig.rectify(L, R)
    left_full, right_full = rig.order_for_matching(rc1, rc2)
    sg = rig.disparity(cv2.resize(left_full, (SGBM_W, SGBM_H)), cv2.resize(right_full, (SGBM_W, SGBM_H)))
    f = rig.focal_px * (SGBM_W / TILE_W)
    with np.errstate(divide="ignore", invalid="ignore"):
        sg_cm = np.where(sg > 0, f * rig.baseline_mm / sg / 10.0, 0.0)
    sg_cm = cv2.resize(sg_cm, (net.in_w, net.in_h))
    raw = net.infer(to_mono_input(left_full, net.in_w, net.in_h))
    m = (sg_cm > 20) & (sg_cm < 600) & (raw > 1)
    if m.sum() < 400:
        return
    corr = float(np.corrcoef(raw[m], sg_cm[m])[0, 1])
    if corr >= 0:                              # raw grows with depth -> cm = k * raw
        k, mode = float(np.median(sg_cm[m] / raw[m])), "linear"
    else:                                      # raw grows toward camera -> cm = k / raw
        k, mode = float(np.median(sg_cm[m] * raw[m])), "inverse"
    _mono_scale[name] = (mode, k)
    print(f"calibrated {name}: {mode} k={k:.1f} (corr {corr:.2f}, {m.sum()} px)")


def _pane(disp, valid, colormap_invert=False):
    if valid.any():
        lo, hi = np.percentile(disp[valid], 5), np.percentile(disp[valid], 95)
    else:
        lo, hi = 0.0, 1.0
    norm = np.clip((disp - lo) / max(hi - lo, 1e-3) * 255.0, 0, 255).astype(np.uint8)
    if colormap_invert:
        norm = 255 - norm
    color = cv2.applyColorMap(norm, cv2.COLORMAP_TURBO)
    color[~valid] = (0, 0, 0)
    return cv2.resize(color, (DISP_W, DISP_H), interpolation=cv2.INTER_NEAREST)


_pc = {"angle": 0.0}


def _pointcloud_frame(rig, left_full, right_full, angle):
    """SGBM disparity -> metric 3D points -> auto-rotated projection (depth-coloured, painter-
    sorted). The actual point cloud: each pixel back-projected to (X,Y,Z) mm, X=(x-cx)Z/f. CPU."""
    dl = cv2.resize(left_full, (SGBM_W, SGBM_H))
    dr = cv2.resize(right_full, (SGBM_W, SGBM_H))
    disp = rig.disparity(dl, dr)
    f = rig.focal_px * (SGBM_W / TILE_W)
    cx, cy = SGBM_W / 2.0, SGBM_H / 2.0
    ys, xs = np.mgrid[0:SGBM_H, 0:SGBM_W]
    valid = disp > 4
    d = disp[valid].astype(np.float32)
    Z = f * rig.baseline_mm / d
    keep = (Z > 200) & (Z < 4000)                  # 0.2-4 m
    Z = Z[keep]
    canvas = np.zeros((DISP_H, DISP_W, 3), np.uint8)
    if Z.size < 200:
        cv2.putText(canvas, "no depth", (30, DISP_H // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (150, 150, 150), 2)
        return canvas
    xv = xs[valid][keep].astype(np.float32)
    yv = ys[valid][keep].astype(np.float32)
    X = (xv - cx) * Z / f
    Y = (yv - cy) * Z / f
    step = max(1, Z.size // 40000)                 # cap points for CPU
    X, Y, Z = X[::step], Y[::step], Z[::step]
    mx, my, mz = X.mean(), Y.mean(), Z.mean()
    Xc, Yc, Zc = X - mx, Y - my, Z - mz
    ca, sa = np.cos(angle), np.sin(angle)          # yaw
    Xr = Xc * ca - Zc * sa
    Zr = Xc * sa + Zc * ca
    ct, st = np.cos(0.3), np.sin(0.3)              # slight down-tilt
    Yr = Yc * ct - Zr * st
    Zr = Yc * st + Zr * ct
    zc = Zr + (mz * 1.5 + 250.0)                   # push cloud in front of the virtual camera
    m = zc > 1
    fv = 480.0
    u = (fv * Xr[m] / zc[m] + DISP_W / 2).astype(np.int32)
    v = (fv * Yr[m] / zc[m] + DISP_H / 2).astype(np.int32)
    zn = np.clip((Z[m] - 300) / 3200.0 * 255, 0, 255).astype(np.uint8)
    col = cv2.applyColorMap(zn.reshape(-1, 1), cv2.COLORMAP_TURBO).reshape(-1, 3)
    order = np.argsort(-zc[m])                      # far -> near (painter's algorithm)
    u, v, col = u[order], v[order], col[order]
    inb = (u >= 0) & (u < DISP_W) & (v >= 0) & (v < DISP_H)
    canvas[v[inb], u[inb]] = col[inb]
    return cv2.dilate(canvas, np.ones((2, 2), np.uint8))   # thicken points for visibility


def depth_loop(rig, models_dir):
    ema = None
    min_period = 1.0 / DEPTH_FPS_CAP
    last_frame = 0.0
    while True:
        with _clock:
            name, net, loading = _cur["name"], _cur["net"], _cur["loading"]
        if _viewers["n"] == 0:            # nobody watching -> don't compute (idle, cool)
            time.sleep(0.2)
            continue
        with _lock:
            L, R = _state["L"], _state["R"]
        if L is None:
            time.sleep(0.05)
            continue
        if loading:
            blank = np.full((DISP_H, DISP_W, 3), 20, np.uint8)
            cv2.putText(blank, f"loading {name} ...", (30, DISP_H // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 200), 2)
            ok, jpg = cv2.imencode(".jpg", blank, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                _jpg["buf"] = jpg.tobytes()
            time.sleep(0.1)
            continue

        # Rate cap: throttle the whole compute+encode chain to DEPTH_FPS_CAP so it doesn't
        # spin flat-out (the rectify/colorize/JPEG wrapper is CPU-bound on every engine).
        wait = min_period - (time.monotonic() - last_frame)
        if wait > 0:
            time.sleep(wait)
        last_frame = time.monotonic()

        rc1, rc2 = rig.rectify(L, R)
        left_full, right_full = rig.order_for_matching(rc1, rc2)
        t0 = time.monotonic()
        readout = ""
        try:
            if name == "pointcloud":
                _pc["angle"] += 0.06
                pane = _pointcloud_frame(rig, left_full, right_full, _pc["angle"])
                readout = "SGBM point cloud (auto-rotating) - metric mm, depth-coloured"
            elif name == "sgbm":
                dl = cv2.resize(left_full, (SGBM_W, SGBM_H))
                dr = cv2.resize(right_full, (SGBM_W, SGBM_H))
                disp = rig.disparity(dl, dr)
                valid = disp > 0
                d0 = disp[SGBM_H // 2, SGBM_W // 2]
                f = rig.focal_px * (SGBM_W / TILE_W)
                readout = f"center {f*rig.baseline_mm/d0/10:.0f} cm   dense {valid.mean()*100:.0f}%" \
                    if d0 > 0 else "center --"
                pane = _pane(disp, valid)
            elif name == "stereonet":
                lm, rm = to_model_input(left_full), to_model_input(right_full)
                disp = net.infer(lm, rm)
                valid = np.ones_like(disp, dtype=bool)
                d0 = disp[IN_H // 2, IN_W // 2]
                f = rig.focal_px * (IN_W / TILE_W)
                readout = f"center ~{f*rig.baseline_mm/d0/10:.0f} cm (provisional)" if d0 > 0 else "--"
                pane = _pane(disp, valid)
            else:  # mono
                inp = to_mono_input(left_full, net.in_w, net.in_h)
                depth = net.infer(inp)
                valid = np.ones_like(depth, dtype=bool)
                if name not in _mono_scale:
                    _calibrate_mono(rig, net, name)
                sc = _mono_scale.get(name)
                rawc = float(depth[net.in_h // 2, net.in_w // 2])
                if sc and rawc > 0:
                    cm = sc[1] / rawc if sc[0] == "inverse" else sc[1] * rawc
                    readout = f"center ~{cm:.0f} cm  (mono, SGBM-calibrated)"
                else:
                    readout = f"center raw {rawc:.0f}  (relative)"
                invert = (sc[0] == "linear") if sc else (name != "scdepthv3")
                pane = _pane(depth, valid, colormap_invert=invert)
        except Exception as e:
            time.sleep(0.1)
            print(f"{name} infer error: {e}")
            continue
        dt = time.monotonic() - t0
        ema = dt if ema is None else 0.8 * ema + 0.2 * dt
        with _clock:
            _cur["fps"] = 1.0 / ema if ema else 0.0
            fps = _cur["fps"]
        if name != "pointcloud":
            cv2.drawMarker(pane, (DISP_W // 2, DISP_H // 2), (255, 255, 255), cv2.MARKER_CROSS, 18, 1)
        cv2.putText(pane, f"{name}   {fps:.1f} fps", (10, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(pane, readout, (10, DISP_H - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        ok, jpg = cv2.imencode(".jpg", pane, [cv2.IMWRITE_JPEG_QUALITY, 78])
        if ok:
            _jpg["buf"] = jpg.tobytes()


def _draw_range(left_gray, dets, cls, res):
    """Left image + detection boxes; the ranged instance (best-conf of the locked class) bold
    with its metric Z. Returns JPEG bytes for /range_stream."""
    img = cv2.cvtColor(left_gray, cv2.COLOR_GRAY2BGR)
    best, _ = select_target(dets, cls) if cls else (None, 0)
    for d in dets:
        is_target = best is not None and d is best
        color = (0, 235, 0) if is_target else (150, 150, 150)
        cv2.rectangle(img, (int(d.x), int(d.y)), (int(d.x + d.w), int(d.y + d.h)),
                      color, 2 if is_target else 1)
        cv2.putText(img, f"{d.label} {d.conf:.2f}", (int(d.x), int(d.y) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2 if is_target else 1)
    if not cls:
        banner = "no target - click one to lock"     # ASCII only: OpenCV Hershey has no em-dash
    elif res and res.valid:
        banner = f"{cls}: {res.z_m:.2f} m   (stereo)" + (f"  1/{res.count}" if res.count > 1 else "")
    else:
        banner = f"{cls}: searching..."
    cv2.putText(img, banner, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 78])
    return jpg.tobytes() if ok else None


def range_loop(rig):
    """Gated on a locked class: detect yolov8m on the stereo-LEFT, range the best instance via
    L/R bbox disparity, publish the metric Z. Idle (no NPU) while no class is set."""
    detector = None
    min_period = 1.0 / RANGE_FPS
    last = 0.0
    last_seq = -1
    while True:
        with _range_lock:
            cls = _range["class"]
        if cls is None and _range_viewers["n"] == 0:   # no lock AND nobody previewing -> idle
            time.sleep(0.2)
            continue
        with _lock:
            L, R, seq = _state["L"], _state["R"], _state["seq"]
        if L is None:
            time.sleep(0.05)
            continue
        if seq == last_seq:            # camera delivered no new frame -> don't re-range a stale one
            time.sleep(0.03)           # (else age_ms stays fresh and a frozen scene reads as "live")
            continue
        last_seq = seq
        wait = min_period - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.monotonic()
        if detector is None:
            try:
                # yolov8m shares the ONE vdevice with the depth engine. Intra-process sharing is
                # verified OK (2026-08-04: fast_depth@115fps + range concurrent, no wedge) — unlike
                # cross-process (garuda-inference), which is HAILO_OUT_OF_PHYSICAL_DEVICES.
                # lower conf floor: the OV9281 is grayscale, so yolov8m confidences run lower
                # than on colour (a chair sat ~0.40-0.53) -> 0.4 flickers, 0.3 is steadier.
                conf = float(os.environ.get("GARUDA_RANGE_CONF", "0.3"))
                detector = HailoDetector(vdevice=_shared_vdevice(), conf_thresh=conf)
            except Exception as e:
                print(f"range detector load failed: {e}")
                time.sleep(1.0)
                continue
        rc1, rc2 = rig.rectify(L, R)
        left, right = rig.order_for_matching(rc1, rc2)
        try:
            dets = detector.infer(left)     # always detect: needed for both preview and ranging
        except Exception as e:
            print(f"range infer error: {e}")
            time.sleep(0.1)
            continue
        res = None                          # range only when a class is locked
        if cls is not None:
            res = range_target(rig.focal_px, rig.baseline_mm, dets, cls,
                               lambda d: roi_disparity(left, right, (d.x, d.y, d.w, d.h)))
        render = False
        with _range_lock:
            if _range["class"] == cls:      # class unchanged this iteration (else drop it all)
                _range["dets"] = dets       # fresh dets for click-to-lock (preview + locked)
                if cls is not None:
                    _range["result"], _range["t"] = res, time.monotonic()
                render = _range_viewers["n"] > 0
        if render:                          # encode outside the lock; cls may be None -> "click to lock"
            _range_jpg["buf"] = _draw_range(left, dets, cls, res)


def _page():
    btns = "".join(
        f"<button onclick=\"pick('{n}')\">{lbl}</button>" for n, lbl, _, _ in MODELS)
    return ("<!doctype html><html><head><title>CamArray depth explorer</title>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<style>body{margin:0;background:#111;color:#ddd;font-family:sans-serif;text-align:center}"
            "button{font-size:15px;margin:5px;padding:9px 14px;border:0;border-radius:7px;"
            "background:#2a6;color:#fff;cursor:pointer}#cur{font-size:16px}</style></head><body>"
            "<p style='margin:6px'>CamArray depth explorer &mdash; pick an engine</p>"
            f"<div>{btns}</div><div id=cur>&nbsp;</div>"
            "<img id='v' src='/stream' style='max-width:100%;height:auto'>"
            "<script>"
            "const v=document.getElementById('v'),cur=document.getElementById('cur');"
            "v.onerror=()=>setTimeout(()=>{v.src='/stream?'+Date.now();},2500);"
            "async function pick(n){cur.textContent='switching to '+n+' (restarting ~9s)...';"
            "try{await fetch('/api/model?name='+n,{method:'POST'});}catch(e){}"
            "setTimeout(()=>{v.src='/stream?'+Date.now();},2500);}"
            "setInterval(async()=>{try{let r=await fetch('/api/current');let j=await r.json();"
            "cur.textContent='current: '+j.name+'   '+j.fps.toFixed(1)+' fps';}"
            "catch(e){cur.textContent='restarting...';}},900);"
            "</script></body></html>").encode()


def make_handler(models_dir):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, "text/html", _page())
            elif path == "/api/current":
                with _clock:
                    body = json.dumps({"name": _cur["name"], "loading": _cur["loading"],
                                       "fps": _cur["fps"]}).encode()
                self._send(200, "application/json", body)
            elif path == "/api/target_range":
                with _range_lock:
                    r, t, cls = _range["result"], _range["t"], _range["class"]
                age = int((time.monotonic() - t) * 1000) if t else None
                if r is None:
                    body = json.dumps({"class": cls, "valid": False, "reason": "no_class",
                                       "age_ms": age}).encode()
                elif age is not None and age > RANGE_STALE_MS:   # camera stalled -> not a live reading
                    body = json.dumps({"class": cls, "valid": False, "reason": "stale",
                                       "age_ms": age}).encode()
                else:
                    body = json.dumps({"class": cls, "z_m": r.z_m, "conf": r.conf,
                                       "count": r.count, "valid": r.valid, "reason": r.reason,
                                       "age_ms": age}).encode()
                self._send(200, "application/json", body)
            elif path == "/range_stream":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                with _range_lock:
                    _range_viewers["n"] += 1
                try:
                    while True:
                        b = _range_jpg["buf"]
                        if b:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                             b"Content-Length: " + str(len(b)).encode() + b"\r\n\r\n")
                            self.wfile.write(b)
                            self.wfile.write(b"\r\n")
                        time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with _range_lock:
                        _range_viewers["n"] = max(0, _range_viewers["n"] - 1)
                return
            elif path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                with _clock:
                    _viewers["n"] += 1
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
                    pass
                finally:
                    with _clock:
                        _viewers["n"] = max(0, _viewers["n"] - 1)
                return
            else:
                self._send(404, "text/plain", b"nope")

        def do_POST(self):
            path = urlparse(self.path)
            if path.path == "/api/model":
                name = parse_qs(path.query).get("name", ["sgbm"])[0]
                self._send(200, "application/json", b'{"ok":true}')
                if name in KIND and name != _cur["name"]:
                    if name in _CPU_ENGINES and _cur["name"] in _CPU_ENGINES:
                        with _clock:                 # CPU<->CPU (sgbm/pointcloud): instant, no Hailo
                            _cur["name"] = name
                    else:                            # to/from an NPU engine: full restart (Hailo reset)
                        threading.Thread(target=lambda: (time.sleep(0.4), _reexec(name)),
                                         daemon=True).start()
            elif path.path == "/api/range_target":   # set the locked class (empty -> clear -> idle)
                name = parse_qs(path.query).get("class", [""])[0] or None
                with _range_lock:
                    if name != _range["class"]:       # class changed: drop the old target's result
                        _range["result"], _range["dets"] = None, []
                        if name is None:
                            _range_jpg["buf"] = None
                    _range["class"] = name
                self._send(200, "application/json", b'{"ok":true}')
            elif path.path == "/api/range_click":    # click (native left-px) -> lock nearest det's class
                q = parse_qs(path.query)
                try:
                    cx, cy = float(q.get("x", ["0"])[0]), float(q.get("y", ["0"])[0])
                except ValueError:
                    self._send(400, "application/json", b'{"ok":false}')
                    return
                if not (-1e7 < cx < 1e7 and -1e7 < cy < 1e7):   # reject nan/inf (would mis-lock)
                    self._send(400, "application/json", b'{"ok":false}')
                    return
                with _range_lock:
                    dets = list(_range["dets"])
                picked, best_d2 = None, None
                for d in dets:
                    d2 = (d.x + d.w / 2.0 - cx) ** 2 + (d.y + d.h / 2.0 - cy) ** 2
                    if best_d2 is None or d2 < best_d2:
                        best_d2, picked = d2, d
                if picked is not None:
                    with _range_lock:
                        if picked.label != _range["class"]:   # class changed: drop stale result
                            _range["result"], _range["dets"] = None, []
                        _range["class"] = picked.label
                self._send(200, "application/json",
                           json.dumps({"ok": True, "class": picked.label if picked else None}).encode())
            else:
                self._send(404, "text/plain", b"nope")

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")  # let the portal (:8080) switch engines
            self.end_headers()
            self.wfile.write(body)
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--model", default="sgbm", help="engine to load at startup (switch re-execs)")
    args = ap.parse_args()
    repo = pathlib.Path(__file__).resolve().parents[2]
    models_dir = repo / "models"
    rig = StereoRig(repo / "bench" / "camarray_stereo.yaml")
    print(f"depth explorer -> http://0.0.0.0:{args.port}/   (engine: {args.model})")

    import os
    os.environ.setdefault("GARUDA_CAM_VBLANK", "3600")
    backend = CamArrayBackend()
    backend.configure(W, H, 30, pixel_format="GREY")
    backend.start(on_frame)
    _reexec_cfg["backend"] = backend
    _reexec_cfg["port"] = args.port
    model = args.model
    try:                                        # last engine picked in the UI wins across restarts
        if _MODEL_FILE.exists():
            m = _MODEL_FILE.read_text().strip()
            if m in KIND:
                model = m
    except Exception:
        pass
    if model in KIND and model != "sgbm":
        switch(model, models_dir)               # one-time synchronous load on a freshly-reset Hailo
    print(f"depth explorer engine: {model}")
    threading.Thread(target=depth_loop, args=(rig, models_dir), daemon=True).start()
    threading.Thread(target=range_loop, args=(rig,), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(models_dir))
    # os._exit on SIGTERM (systemctl stop/restart): a clean shutdown would call net.close() ->
    # vdevice.release(), which HANGS on a wedged Hailo and leaves the unit stuck 'deactivating'.
    # Immediate death is fine — systemd cgroup-cleans the camera child.
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    try:
        srv.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        backend.stop()
        with _clock:
            if _cur["net"] is not None:
                _cur["net"].close()


if __name__ == "__main__":
    main()
