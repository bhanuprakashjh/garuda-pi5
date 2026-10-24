"""CamArraySource — turns one CamArray combined frame into N synchronized views.

Consumes any ICaptureBackend (the Pi libcamera backend on the bench, injected so
this is unit-testable with a fake backend on a dev PC) and, per combined frame,
splits it into per-sub-camera (FrameMeta, memoryview) with a SHARED timestamp.
The split geometry + crop are the pure helpers in stereo.py.

Wiring (once hardware is up): hosts/pi5/camarray_backend.py provides the backend;
this produces the synchronized view list; two of the views feed Basalt (stereo VIO,
needs IMU from the FC over DDS) and/or StereoDepthSource for metric depth.
"""
from __future__ import annotations

import sys
import pathlib
from typing import Callable, List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from garuda_interfaces import FrameMeta  # noqa: E402
from garuda_interfaces.capture import ICaptureBackend  # noqa: E402
from stereo import plan_split, crop_row_major, per_view_metas  # noqa: E402

# views delivered as parallel lists: metas[i] describes views[i]
ViewsCallback = Callable[[List[FrameMeta], List[memoryview]], None]

_BPP = {"MONO8": 1, "GREY": 1, "Y8": 1, "RGB888": 3, "BGR888": 3}


class CamArraySource:
    """Splits a CamArray combined stream into `rows*cols` hardware-synced views.

    sensors: one label per view, row-major (top-left first) — length must equal
    rows*cols; these become the per-view FrameMeta.sensor (e.g. "ov9281.cam0"..3).
    """

    def __init__(self, backend: ICaptureBackend, rows: int, cols: int,
                 sensors: List[str], combined_w: int, combined_h: int,
                 fps: float = 60.0, pixel_format: str = "MONO8"):
        if len(sensors) != rows * cols:
            raise ValueError(
                f"need {rows * cols} sensor labels for a {rows}x{cols} array, got {len(sensors)}")
        self.backend = backend
        self.rows, self.cols = rows, cols
        self.sensors = list(sensors)
        self.combined_w, self.combined_h = combined_w, combined_h
        self.fps = fps
        self.pixel_format = pixel_format
        self._bpp = _BPP.get(pixel_format.upper())
        if self._bpp is None:
            raise ValueError(f"unsupported pixel_format {pixel_format!r} (mono OV9281 -> MONO8)")
        self._rects = plan_split(combined_w, combined_h, rows, cols)
        self._sub_w = combined_w // cols
        self._sub_h = combined_h // rows

    def start(self, on_views: ViewsCallback) -> None:
        """Configure + start the backend for the combined frame, and fan every
        combined frame out into the per-view callback."""
        self.backend.configure(self.combined_w, self.combined_h, self.fps,
                               pixel_format=self.pixel_format)

        def _on_frame(meta: FrameMeta, buf: memoryview) -> None:
            views = [memoryview(crop_row_major(buf, self.combined_w, self._bpp, r))
                     for r in self._rects]
            metas = per_view_metas(meta, self.sensors, self._sub_w, self._sub_h)
            on_views(metas, views)

        self.backend.start(_on_frame)

    def stop(self) -> None:
        self.backend.stop()
