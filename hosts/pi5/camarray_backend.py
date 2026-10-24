"""Pi 5 raw-V4L2 capture backend for the Arducam CamArray HAT (OV9281 ×4).

ICaptureBackend implementation — the ONLY Pi-specific camera code (CLAUDE.md: no
Pi-specific calls outside hosts/pi5/). WORKING on Trixie via the stock in-kernel
`arducam-pivariety` driver (no Bookworm, no installer, no libcamera swap — the CM3
stays on libcamera). services/garuda-camera/CamArraySource splits the combined
frame into synchronized per-sensor views.

=== BRING-UP (proven 2026-08-01; docs/decisions.md + docs/camarray-bringup.md) ===
* config.txt: camera_auto_detect=0, dtparam=i2c_vc=on, dtoverlay=arducam-pivariety,cam0
  (+ dtoverlay=imx708,cam1 to keep the CM3). Hardware: HAT on USB power, SDA/SCL berg
  to Pi i2c, CSI ribbon fully seated on CAM1.
* The ArduChip is a Pivariety 16-bit controller at 0x0c on the CSI i2c; the stock
  arducam-pivariety driver binds it and reports a combined Y8 frame (5120x800 for the
  quad HAT = 4 tiles of 1280x800; connected cameras populate tiles 0,1 = a stereo pair).
* Capture is RAW (stock libcamera has no pivariety CamHelper): hosts/pi5/camarray_setup.sh
  sets the media-ctl pipeline (disable the ISP branch so STREAMON doesn't Broken-pipe),
  then v4l2-ctl streams GREY frames. This backend runs that setup + reads the stream.

Requires: i2c-tools + v4l2-utils (v4l2-ctl, media-ctl); the running user in the `video`
group (or the setup shells out with sudo). No new Python deps.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "interfaces"))
from garuda_interfaces import FrameMeta  # noqa: E402
from garuda_interfaces.capture import ICaptureBackend, FrameCallback  # noqa: E402

_SETUP = pathlib.Path(__file__).resolve().parent / "camarray_setup.sh"
_BPP = {"MONO8": 1, "GREY": 1, "Y8": 1, "RGB888": 3}


def read_exact(stream, n: int):
    """Read exactly n bytes from a binary stream, looping over short reads.
    Returns the bytes, or None at EOF / on a truncated final read. Pure — unit
    tested with a fake stream (no hardware)."""
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class CamArrayBackend(ICaptureBackend):
    """Captures the CamArray combined stream raw via v4l2-ctl. `configure` runs the
    media-ctl pipeline recipe (camarray_setup.sh) and learns the capture node from
    its output; `start` streams fixed-size frames and hands each to `on_frame`."""

    def __init__(self, setup_script: str = None, sensor: str = "ov9281.camarray"):
        self._setup = str(setup_script or _SETUP)
        self._sensor = sensor
        self._node = "/dev/video0"
        self._w = self._h = 0
        self._frame_bytes = 0
        self._pixel_format = "MONO8"
        self._proc = None
        self._thread = None
        self._on_frame = None
        self._running = False

    def configure(self, width, height, fps, pixel_format="MONO8", trigger_external=False):
        # trigger_external is a no-op: the CamArray syncs its sensors on the HAT.
        self._w, self._h = width, height
        self._pixel_format = pixel_format
        bpp = _BPP.get(pixel_format.upper())
        if bpp is None:
            raise ValueError(f"unsupported pixel_format {pixel_format!r} (mono OV9281 -> MONO8)")
        self._frame_bytes = width * height * bpp
        # Run the pipeline recipe; it prints "... capture_node=/dev/videoN".
        out = subprocess.run([self._setup, str(width), str(height)],
                             capture_output=True, text=True, check=True).stdout
        m = re.search(r"capture_node=(/dev/video\d+)", out)
        if m:
            self._node = m.group(1)

    def start(self, on_frame: FrameCallback):
        self._on_frame = on_frame
        self._running = True
        # Stream raw frames to stdout; a reader thread slices them by frame size.
        self._proc = subprocess.Popen(
            ["v4l2-ctl", "-d", self._node, "--stream-mmap",
             "--stream-count=0", "--stream-to=-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self):
        frame_id = 0
        stream = self._proc.stdout
        while self._running:
            buf = read_exact(stream, self._frame_bytes)
            if buf is None:
                break
            meta = FrameMeta(frame_id=frame_id, t_capture_ns=time.monotonic_ns(),
                             clock_domain="mono", sensor=self._sensor,
                             width=self._w, height=self._h, pixel_format=self._pixel_format)
            frame_id += 1
            try:
                self._on_frame(meta, memoryview(buf))
            except Exception:
                # a bad consumer must not kill the capture thread (portal pattern)
                pass

    def stop(self):
        # Idempotent: safe to call repeatedly.
        self._running = False
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except Exception:
                self._proc.kill()
            self._proc = None
        self._on_frame = None
