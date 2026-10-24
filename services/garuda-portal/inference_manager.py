"""InferenceManager — the single-camera-child ownership switch.

Camera ownership rule (hardware constraint, not a style choice): the MJPEG
CameraStream and the garuda-inference pipeline are both single-client camera
consumers, so exactly one may hold the camera at a time. `mode` is always
"stream" or "inference"; `start()`/`stop()` are the only transitions and both
sides (camera stop/start, child spawn/kill) are handled together so the two
data paths can never both be active.

Spawn is injected (`spawn=subprocess.Popen` by default) so mode-switch logic
is unit-testable on a box with no camera and no garuda-inference install.
"""
from __future__ import annotations
import json
import subprocess
import threading

from camera_stream import FrameSource, split_mjpeg

# --preview-mjpeg: annotated JPEG frames on stdout (for /api/stream), detection
# JSONL on stderr. Lets the portal show a live boxes-on-video preview AND keep the
# structured detection feed from one camera holder.
DEFAULT_CMD = ["python3", "/opt/garuda/garuda-inference/garuda_inference.py",
               "--preview-mjpeg"]


class InferenceManager:
    def __init__(self, camera_stream, bus, cmd=None, spawn=subprocess.Popen):
        self.camera_stream = camera_stream
        self.bus = bus
        self.cmd = cmd or DEFAULT_CMD
        self._spawn = spawn
        self.mode = "stream"
        self._proc = None
        self.preview = FrameSource()   # annotated frames fed from the child's stdout
        self._frame_thread = None
        self._det_thread = None
        self._lock = threading.Lock()

    def active_source(self):
        """The frame source /api/stream should serve right now: the annotated
        inference preview while in inference mode, else the raw camera stream."""
        with self._lock:
            return self.preview if self.mode == "inference" else self.camera_stream

    def start(self) -> dict:
        """Stop the camera stream and spawn the inference child. Reverts on spawn failure."""
        with self._lock:
            if self.mode == "inference":
                return {"ok": True, "mode": self.mode}
            self.camera_stream.stop()
            self.preview = FrameSource()   # fresh buffer for this session
            try:
                # Binary pipes: stdout carries the JPEG byte stream, stderr the
                # detection JSONL. (text=True would corrupt the binary frames.)
                proc = self._spawn(self.cmd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
            except OSError as e:
                # Spawn failed (e.g. script/interpreter missing on this dev box):
                # revert to stream mode rather than leaving the camera idle.
                self.camera_stream.start()
                self.bus.publish("error", {"stage": "inference-start", "detail": str(e)})
                return {"ok": False, "error": str(e)}
            self._proc = proc
            self.mode = "inference"
            # Two reader threads, one per pipe; either ending triggers the same
            # idempotent child-exit recovery. Handles kept so callers/tests can join.
            self._frame_thread = threading.Thread(target=self._frame_loop, args=(proc,), daemon=True)
            self._det_thread = threading.Thread(target=self._detection_loop, args=(proc,), daemon=True)
            self._frame_thread.start()
            self._det_thread.start()
            self.bus.publish("camera_mode", {"mode": self.mode})
            return {"ok": True, "mode": self.mode}

    def stop(self) -> dict:
        """Kill the inference child and restart the camera stream."""
        with self._lock:
            return self._stop_locked()

    def get_mode(self) -> str:
        """Thread-safe read of the current mode (health-publisher polls this)."""
        with self._lock:
            return self.mode

    def _stop_locked(self) -> dict:
        """Shared stream-mode-recovery body. Caller must hold self._lock.
        Idempotent: a no-op if already in stream mode, so an explicit stop()
        racing a reader thread's own child-exit recovery (see _on_child_exit)
        can never double-kill/double-restart/double-publish."""
        if self.mode == "stream":
            return {"ok": True, "mode": self.mode}
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None
        self.mode = "stream"
        self.camera_stream.start()
        self.bus.publish("camera_mode", {"mode": self.mode})
        return {"ok": True, "mode": self.mode}

    def _frame_loop(self, proc):
        """Read the child's stdout (raw concatenated annotated JPEGs) and feed
        the preview FrameSource that /api/stream serves in inference mode."""
        try:
            buf = b""
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                frames, buf = split_mjpeg(buf + chunk)
                if frames:
                    self.preview.feed(frames[-1])
        except Exception:
            pass
        finally:
            self._on_child_exit(proc)

    def _detection_loop(self, proc):
        """Parse the child's stderr JSONL (garuda_interfaces.JsonlPublisher format:
        {"topic": ..., "msg": ...}) and republish DetectionSets on the bus."""
        try:
            for raw in proc.stderr:
                line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("topic") == "detections":
                    self.bus.publish("detections", obj.get("msg", {}))
        except Exception:
            pass
        finally:
            self._on_child_exit(proc)

    def _on_child_exit(self, proc):
        """The inference child can die on its own (crash, missing model, killed
        out-of-band) without going through stop() — leaving mode stuck at
        "inference" with no camera running would be a silent failure. If an
        explicit stop() (or a prior call to this method for the same proc)
        already recovered, this is a no-op: both the mode check and the proc
        identity check guard against racing/duplicate recovery."""
        with self._lock:
            if self.mode != "inference" or self._proc is not proc:
                return
            self.bus.publish("error", {"stage": "inference-exit",
                                       "detail": "inference child exited"})
            self._stop_locked()
