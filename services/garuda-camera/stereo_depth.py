"""StereoDepthSource — passive-stereo depth from a synchronized OV9281 pair.

Implements IDepthSource: a rectified left/right view pair -> per-pixel disparity
-> depth (uint16 mm) via stereo.disparity_to_depth_mm (Z = f*B/d). This is the
"metric range" upgrade for follow (Hailo says which pixels are the subject; this
says how many mm), and the obstacle/landing depth input.

Scope (honest, CLAUDE.md rule 9): the depth *math* is real and unit-tested; the
disparity *matcher* + rectification need calibration (calib/ workstream) and
OpenCV, so `_match_disparity` is injected (default: cv2 StereoSGBM, import-guarded)
and the whole live path is @requires_hw(camarray). Baseline/focal come from stereo
calibration once the board is on the bench — until then they're explicit inputs,
never invented numbers (rule 1).
"""
from __future__ import annotations

import sys
import pathlib
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from garuda_interfaces import FrameMeta  # noqa: E402
from garuda_interfaces.depth import IDepthSource, DepthCallback  # noqa: E402
from stereo import disparity_to_depth_mm  # noqa: E402

# left_view, right_view -> per-pixel disparity map (any 2D array-like of px shifts)
DisparityMatcher = Callable[[object, object], object]


class StereoDepthSource(IDepthSource):
    """focal_px + baseline_mm come from stereo calibration (calib/). `matcher`
    computes disparity from a rectified pair; default uses cv2 StereoSGBM if OpenCV
    is present. `rectify` (optional) maps raw views -> rectified views."""

    def __init__(self, focal_px: float, baseline_mm: float,
                 matcher: Optional[DisparityMatcher] = None,
                 rectify: Optional[Callable] = None):
        if focal_px <= 0 or baseline_mm <= 0:
            raise ValueError("focal_px and baseline_mm must be positive (from calibration)")
        self.focal_px = focal_px
        self.baseline_mm = baseline_mm
        self._matcher = matcher or _default_sgbm_matcher()
        self._rectify = rectify
        self._on_depth: Optional[DepthCallback] = None
        self._running = False

    def start(self, on_depth: DepthCallback) -> None:
        self._on_depth = on_depth
        self._running = True

    def stop(self) -> None:
        self._running = False
        self._on_depth = None

    def on_stereo_pair(self, meta: FrameMeta, left, right) -> None:
        """Feed one synchronized pair (from CamArraySource). Computes a depth frame
        (uint16 mm) and emits it via the on_depth callback. `meta` carries the shared
        timestamp; the emitted depth FrameMeta reuses it so depth stays in the same
        PTP clock domain as the source views (CLAUDE.md one-clock rule)."""
        if not self._running or self._on_depth is None:
            return
        import numpy as np  # local: only the live depth path needs numpy
        if self._rectify is not None:
            left, right = self._rectify(left, right)
        disp = np.asarray(self._matcher(left, right), dtype="float32")
        depth_mm = np.zeros(disp.shape, dtype="uint16")
        valid = disp > 0
        # vectorized Z = f*B/d, clipped to uint16 range; invalid/no-match -> 0
        z = np.zeros_like(disp)
        z[valid] = self.focal_px * self.baseline_mm / disp[valid]
        depth_mm[:] = np.clip(z, 0, 65535).astype("uint16")
        self._on_depth(meta, memoryview(depth_mm))

    # exposed for unit testing the scalar math without OpenCV/numpy
    depth_mm_at = staticmethod(disparity_to_depth_mm)


def _default_sgbm_matcher() -> DisparityMatcher:
    """cv2 StereoSGBM matcher (BSD/Apache — licence-clean), lazily imported so this
    module imports on a dev PC without OpenCV. Returns a matcher that raises a clear
    error if used without cv2, rather than at import time."""
    def match(left, right):
        try:
            import cv2
        except ImportError as e:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "StereoDepthSource needs OpenCV (python3-opencv) for disparity; "
                "inject a `matcher` to override.") from e
        sgbm = cv2.StereoSGBM_create(minDisparity=0, numDisparities=96, blockSize=7)
        # SGBM returns fixed-point disparity (x16); scale back to pixels
        return sgbm.compute(left, right).astype("float32") / 16.0
    return match
