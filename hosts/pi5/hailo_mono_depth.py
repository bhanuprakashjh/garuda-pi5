#!/usr/bin/env python3
"""HailoMonoDepth — monocular depth on the Hailo-10H (e.g. fast_depth). Pi-specific (HailoRT).

fast_depth is MIT-licensed (github.com/dwofk/fast-depth) and NYU-indoor-trained — unlike the
unlicensed, driving-trained StereoNet, this one is licence-clean AND matches our indoor scenes.
Monocular: ONE image -> a learned per-pixel depth (no stereo pair, so it doesn't *measure*
distance — it infers it from monocular cues), UINT16 output.

I/O (hailortcli parse-hef): UINT8 NHWC 224x224x3 in -> UINT16 NHWC 224x224x1 out. ~2289 fps.
"""
from __future__ import annotations

import cv2
import numpy as np
from hailo_platform import VDevice


def to_mono_input(gray_tile, w, h):
    """A camera tile -> (h,w,3) uint8 RGB for the mono depth net (models differ: fast_depth
    224x224, scdepthv3 256x320)."""
    return cv2.cvtColor(cv2.resize(gray_tile, (w, h)), cv2.COLOR_GRAY2RGB)


class HailoMonoDepth:
    def __init__(self, hef_path, timeout_ms: int = 5000, vdevice=None):
        # Share ONE VDevice across engine switches (see HailoStereoNet): releasing +
        # recreating a VDevice per switch corrupts the Hailo. If vdevice is given, reuse it.
        self.timeout_ms = timeout_ms
        self._own_vdevice = vdevice is None
        self.vdevice = vdevice if vdevice is not None else VDevice(VDevice.create_params())
        self.im = self.vdevice.create_infer_model(str(hef_path))
        self.im.set_batch_size(1)
        self.cm = self.im.configure()
        self.in_name = self.im.input_names[0]
        self.out_name = self.im.output_names[0]
        _s = self.im.input(self.in_name).shape                         # [H, W, C]
        self.in_h, self.in_w = int(_s[0]), int(_s[1])
        self.out_shape = tuple(self.im.output(self.out_name).shape)    # (H,W,1)

    def infer(self, rgb):
        b = self.cm.create_bindings()
        b.input(self.in_name).set_buffer(np.ascontiguousarray(rgb, dtype=np.uint8))
        out = np.zeros(self.out_shape, dtype=np.uint16)
        b.output(self.out_name).set_buffer(out)
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
