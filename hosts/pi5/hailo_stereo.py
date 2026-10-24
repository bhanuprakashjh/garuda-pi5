#!/usr/bin/env python3
"""HailoStereoNet — StereoNet stereo disparity on the Hailo-10H NPU. Pi-specific (HailoRT).

BENCH-ONLY (CLAUDE.md rule 6): StereoNet's source (github.com/nivosco/StereoNet) is UNLICENSED;
we run Hailo's official pre-compiled HEF (models/stereonet_h10.hef) as an experiment to prove the
NPU depth path and benchmark it. NOT product-cleared — product needs a licence-clean depth model.

Model I/O (hailortcli parse-hef): two UINT8 NHWC 368x1232x3 inputs (input_layer1/2) -> one UINT8
NHWC 368x1232x1 output (conv53 = disparity). ~16.7 FPS on the 10H. The matcher runs on the NPU;
rectification stays on the CPU (cheap) and reuses our stereo calibration for metric depth.

    python3 hosts/pi5/hailo_stereo.py   # probe: infer a captured pair, report which input is 'left'
"""
from __future__ import annotations

import pathlib
import sys
import time

import cv2
import numpy as np
from hailo_platform import VDevice

IN_W, IN_H = 1232, 368
L_IN, R_IN, OUT = "stereonet/input_layer1", "stereonet/input_layer2", "stereonet/conv53"


def to_model_input(gray_tile):
    """A rectified grayscale tile -> (368,1232,3) uint8 RGB, StereoNet's input format.
    Center-crop to the model's wide aspect FIRST, then resize ~1:1 — squishing 800->368
    (a 2.2x vertical crush) distorts the geometry and makes the output amorphous/range-
    compressed. Cropping keeps the central band undistorted (loses ceiling/floor)."""
    h, w = gray_tile.shape[:2]
    crop_h = min(h, int(round(w * IN_H / IN_W)))      # 1280 * 368/1232 = 382
    y0 = (h - crop_h) // 2
    band = gray_tile[y0:y0 + crop_h, :]
    return cv2.cvtColor(cv2.resize(band, (IN_W, IN_H)), cv2.COLOR_GRAY2RGB)


class HailoStereoNet:
    """Keeps the device configured + the vstream pipeline open across frames (activating
    per-infer is what kills throughput). Call infer(left_rgb, right_rgb) -> float32 disparity."""

    def __init__(self, hef_path, swap_inputs: bool = False, timeout_ms: int = 5000, vdevice=None):
        # Share ONE VDevice across engine switches: releasing + recreating a VDevice per
        # switch corrupts the Hailo (HAILO_INTERNAL_FAILURE). If vdevice is given, reuse it
        # and never release it here; only tear down this model's configured-infer-model.
        self.swap_inputs = swap_inputs
        self.timeout_ms = timeout_ms
        self._own_vdevice = vdevice is None
        self.vdevice = vdevice if vdevice is not None else VDevice(VDevice.create_params())
        self.im = self.vdevice.create_infer_model(str(hef_path))
        self.im.set_batch_size(1)
        self.cm = self.im.configure()

    def infer(self, left_rgb, right_rgb):
        li, ri = (R_IN, L_IN) if self.swap_inputs else (L_IN, R_IN)
        b = self.cm.create_bindings()
        b.input(li).set_buffer(np.ascontiguousarray(left_rgb, dtype=np.uint8))
        b.input(ri).set_buffer(np.ascontiguousarray(right_rgb, dtype=np.uint8))
        out = np.zeros((IN_H, IN_W, 1), dtype=np.uint8)
        b.output(OUT).set_buffer(out)
        self.cm.run([b], self.timeout_ms)
        return out[:, :, 0].astype(np.float32)

    def close(self):
        try:
            self.cm.shutdown()
        except Exception:
            pass
        if self._own_vdevice:
            try:
                self.vdevice.release()
            except Exception:
                pass


def _probe():
    repo = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "garuda-camera"))
    sys.path.insert(0, str(repo / "services" / "garuda-camera"))
    from stereo_rig import StereoRig
    rig = StereoRig(repo / "bench" / "camarray_stereo.yaml")
    caps = repo / "calib" / "captures"
    L = cv2.imread(str(caps / "pair11_L.png"), 0)  # tile0
    R = cv2.imread(str(caps / "pair11_R.png"), 0)  # tile1
    rc1, rc2 = rig.rectify(L, R)
    left_full, right_full = rig.order_for_matching(rc1, rc2)   # left = physical-left reference
    lm, rm = to_model_input(left_full), to_model_input(right_full)

    net = HailoStereoNet(repo / "models" / "stereonet_h10.hef")
    # warm up + time
    for _ in range(2):
        disp = net.infer(lm, rm)
    n, t0 = 10, time.monotonic()
    for _ in range(n):
        disp = net.infer(lm, rm)
    fps = n / (time.monotonic() - t0)
    pos = disp > 0
    print(f"OUTPUT disp shape={disp.shape} valid={pos.mean()*100:.1f}% "
          f"min={disp[pos].min() if pos.any() else 0:.0f} max={disp.max():.0f} "
          f"mean={disp[pos].mean() if pos.any() else 0:.1f}")
    print(f"INFER fps={fps:.1f}  (BENCH-ONLY, unlicensed model)")
    net.close()


if __name__ == "__main__":
    _probe()
