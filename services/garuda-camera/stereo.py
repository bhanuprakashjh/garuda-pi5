"""Pure geometry + math for the Arducam CamArray (OV9281 ×4) capture path.

The CamArray HAT hardware-synchronizes N sensors and presents them to the Pi as
ONE combined MIPI frame (the sub-cameras tiled in a grid). Everything here is
pure (no numpy, no libcamera, no hardware) so it runs and is tested on a dev PC,
exactly like the garuda-follow core. The Pi-specific capture lives in
hosts/pi5/camarray_backend.py (CLAUDE.md: no Pi-specific calls outside hosts/pi5/).

Contracts this feeds:
  - ICaptureBackend produces the combined frame; CamArraySource (camarray_source.py)
    splits it into per-view (FrameMeta, memoryview) with a SHARED timestamp
    (the sensors are hardware-synced, so all views carry the same t_capture_ns).
  - StereoDepthSource (stereo_depth.py) turns a rectified view pair into depth (mm)
    via disparity_to_depth_mm below.
"""
from __future__ import annotations

import dataclasses
import sys
import pathlib
from dataclasses import dataclass
from typing import List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "interfaces"))
from garuda_interfaces import FrameMeta  # noqa: E402


@dataclass(frozen=True)
class CropRect:
    x: int
    y: int
    w: int
    h: int


def plan_split(combined_w: int, combined_h: int, rows: int, cols: int) -> List[CropRect]:
    """Tile a combined frame into rows×cols equal sub-views, row-major (top-left
    first). Stereo pair = (1, 2); quad side-by-side = (1, 4); quad square = (2, 2).

    The combined dimensions must divide evenly by cols/rows — the CamArray emits
    equal tiles, so a non-divisible size means the wrong combined resolution was
    configured; we fail loudly rather than silently mis-crop.
    """
    if rows < 1 or cols < 1:
        raise ValueError(f"rows/cols must be >= 1, got {rows}x{cols}")
    if combined_w % cols or combined_h % rows:
        raise ValueError(
            f"combined {combined_w}x{combined_h} not divisible by {cols}x{rows} tiles")
    vw, vh = combined_w // cols, combined_h // rows
    return [CropRect(c * vw, r * vh, vw, vh) for r in range(rows) for c in range(cols)]


def crop_row_major(buf, combined_w: int, bpp: int, rect: CropRect) -> bytes:
    """Extract a sub-rectangle from a row-major (top-left origin) pixel buffer.

    buf: the combined frame as bytes/memoryview, `bpp` bytes per pixel
    (OV9281 mono = 1; RGB888 = 3). Copies each row of the rect out of the stride;
    a column-band tile is NOT contiguous in a row-major image, hence the per-row copy.
    """
    stride = combined_w * bpp
    row_bytes = rect.w * bpp
    out = bytearray(rect.h * row_bytes)
    for r in range(rect.h):
        src = (rect.y + r) * stride + rect.x * bpp
        out[r * row_bytes:(r + 1) * row_bytes] = buf[src:src + row_bytes]
    return bytes(out)


def per_view_metas(base: FrameMeta, sensors: List[str], sub_w: int, sub_h: int) -> List[FrameMeta]:
    """One FrameMeta per view: SAME timestamp/frame_id/clock_domain/format as the
    combined frame (hardware-synced — the whole point), distinct `sensor` label and
    the sub-view dimensions. `sensors` length must match the view count.
    """
    metas = []
    for name in sensors:
        m = dataclasses.replace(base, sensor=name, width=sub_w, height=sub_h)
        metas.append(m)
    return metas


def disparity_to_depth_mm(disparity_px: float, focal_px: float, baseline_mm: float) -> float:
    """Rectified-stereo depth: Z = f * B / d. Returns depth in mm, or 0.0 for a
    non-positive disparity (no/invalid match) so callers can treat 0 as 'unknown'
    — same 'producer marks its own invalidity' discipline as the odometry/health path.

    focal_px: rectified focal length in pixels; baseline_mm: stereo baseline in mm;
    disparity_px: left-right pixel shift for the point.
    """
    if disparity_px <= 0.0 or focal_px <= 0.0 or baseline_mm <= 0.0:
        return 0.0
    return focal_px * baseline_mm / disparity_px
