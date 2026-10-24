#!/usr/bin/env python3
"""garuda-track: live CM3 -> Hailo YOLOv8m (reuses garuda-inference's proven GStreamer pipeline)
-> ByteTrack -> lock FSM -> TrackedObjects IPC, with a UDS lock-command channel (portal click).

Two layers:
  - process(): the pure, offline-testable core (detections -> tracks -> lock -> published msg).
    No camera/NPU; unit-tested with scripted detections.
  - run() / the GStreamer pipeline: the live path. In on_sample we pull Hailo detections from the
    ROI metadata (same as garuda-inference), convert to pixel bboxes, and call process(). gi/hailo
    are imported lazily inside run() so the offline test imports nothing hardware-bound.

Single-tenant NPU + camera: stop garuda-depth, garuda-inference, and the portal's camera stream
before running. hosts/pi5/run_track.sh (Task 8) will automate this; until then, stop them manually.
"""
from __future__ import annotations

import sys
import time

# --- imports resolved for both PC (repo) and bench (deploy) layouts ---
import pathlib
_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))                                   # sibling modules
sys.path.insert(0, str(_HERE.parents[1] / "interfaces"))         # repo interfaces
sys.path.insert(0, "/home/garuda/garuda_pylib")                  # bench deploy interfaces

from bytetrack import ByteTracker                                # noqa: E402
from cmd_channel import CmdServer                                # noqa: E402
from lock_fsm import LockFSM                                     # noqa: E402
from track_ipc import TrackIPC                                   # noqa: E402
from garuda_interfaces.tracked_objects import (                  # noqa: E402
    TrackedObject, TrackedObjects, ObjState, LockCommand)

COCO_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush"]


def coco_name(cls: int, fallback: str = "") -> str:
    return COCO_NAMES[cls] if 0 <= cls < len(COCO_NAMES) else (fallback or f"cls{cls}")


class TrackService:
    def __init__(self, ipc_path: str, cmd_path: str, min_hits: int = 3, n_coast: int = 15,
                 max_bytes: int = 65536, open_cmd: bool = True):
        self.tracker = ByteTracker(min_hits=min_hits)
        self.fsm = LockFSM(n_coast=n_coast)
        self.ipc = TrackIPC(ipc_path, create=True, max_bytes=max_bytes)
        self.cmd = CmdServer(cmd_path) if open_cmd else None
        self.seq = 0

    def process(self, t_ns, frame_w, frame_h, detections, cmds=None, labels=None):
        """detections: list of (bbox[x,y,w,h]px, cls:int, conf:float).
        labels: optional {cls_id: name} from the detector — authoritative (e.g. Hailo's own
        get_label()); falls back to the COCO table only when a class is absent.
        Returns the published TrackedObjects."""
        tracks = self.tracker.update(detections)
        if cmds is None:
            cmds = self.cmd.poll() if self.cmd else []
        # Latest command this frame wins: the UDS datagram queue is FIFO (same host), and a
        # debounced click-to-lock never emits >1 command per ~35 ms frame; lock-then-unlock nets to unlock.
        cmd = cmds[-1] if cmds else None
        locked_id, lock_state = self.fsm.step(tracks, cmd)
        self.seq += 1
        objs = [TrackedObject(
            track_id=t.track_id, cls=int(t.cls),
            cls_name=(labels.get(int(t.cls)) if labels else None) or coco_name(int(t.cls)),
            conf=round(float(t.conf), 4), bbox=[round(float(v), 1) for v in t.bbox],
            age=int(t.age),
            state=ObjState.CONFIRMED if t.confirmed else ObjState.TENTATIVE)
            for t in tracks]
        msg = TrackedObjects(
            t_capture_ns=int(t_ns), frame_w=int(frame_w), frame_h=int(frame_h),
            objects=objs, locked_id=int(locked_id), lock_state=lock_state, seq=self.seq)
        self.ipc.write(msg)
        return msg

    def close(self):
        self.ipc.close()
        if self.cmd:
            self.cmd.close()


# --------------------------- live path (bench only) ---------------------------

_INPUT = 640  # yolov8m HEF input; Hailo bboxes are normalized -> multiply by this
_POSTPROC = "/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/libyolo_hailortpp_post.so"
_PIPELINE = (
    "libcamerasrc ! videoconvert ! videoscale ! "
    "video/x-raw,format=RGB,width={w},height={w} ! "
    "queue max-size-buffers=3 leaky=downstream ! "
    "hailonet hef-path={hef} ! "
    "queue max-size-buffers=3 leaky=downstream ! "
    "hailofilter so-path={post} qos=false ! "
    "appsink name=sink emit-signals=true max-buffers=2 drop=true sync=false"
)


def run(hef="/usr/share/hailo-models/yolov8m_h10.hef",
        ipc_path="/dev/shm/garuda_track.ipc", cmd_path="/tmp/garuda_track.cmd",
        min_hits=3, n_coast=15, view_port=8092):
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst, GLib
    import hailo

    svc = TrackService(ipc_path, cmd_path, min_hits=min_hits, n_coast=n_coast)
    view = None
    if view_port:
        import cv2
        import numpy as np
        from cmd_channel import CmdClient
        from track_view import ViewServer, draw_overlay
        _vc = CmdClient(cmd_path)                                 # click -> same cmd channel process() drains
        view = ViewServer(view_port, (_INPUT, _INPUT),
                          on_lock=lambda fx, fy: _vc.send(LockCommand("lock", x=fx, y=fy)),
                          on_unlock=lambda: _vc.send(LockCommand("unlock")))
        view.start()

    Gst.init(None)
    pipe = Gst.parse_launch(_PIPELINE.format(w=_INPUT, hef=hef, post=_POSTPROC))
    stats = {"n": 0, "t0": time.monotonic()}

    def on_sample(sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        t_cap = buf.pts if buf.pts != Gst.CLOCK_TIME_NONE else time.monotonic_ns()
        dets = []
        labels = {}
        roi = hailo.get_roi_from_buffer(buf)
        for d in roi.get_objects_typed(hailo.HAILO_DETECTION):
            b = d.get_bbox()
            cid = int(d.get_class_id())
            dets.append(([b.xmin() * _INPUT, b.ymin() * _INPUT, b.width() * _INPUT, b.height() * _INPUT],
                         cid, float(d.get_confidence())))
            lbl = d.get_label()
            if lbl:
                labels[cid] = lbl                                 # Hailo's own label = authoritative
        msg = svc.process(t_cap, _INPUT, _INPUT, dets, labels=labels)
        if view is not None:
            ok, minfo = buf.map(Gst.MapFlags.READ)
            if ok:
                try:
                    rgb = np.frombuffer(minfo.data, np.uint8).reshape(_INPUT, _INPUT, 3)
                    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                    draw_overlay(bgr, msg)
                    okj, jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if okj:
                        view.set_frame(jpg.tobytes())
                finally:
                    buf.unmap(minfo)
        stats["n"] += 1
        if stats["n"] % 150 == 0:
            el = time.monotonic() - stats["t0"]
            print(f"[garuda-track] {stats['n']} frames, {stats['n']/el:.1f} fps, "
                  f"{len(msg.objects)} tracks, lock={msg.lock_state.value}({msg.locked_id})",
                  file=sys.stderr, flush=True)
        return Gst.FlowReturn.OK

    pipe.get_by_name("sink").connect("new-sample", on_sample)
    loop = GLib.MainLoop()
    bus = pipe.get_bus(); bus.add_signal_watch()
    bus.connect("message::error", lambda b, m: (print(f"[garuda-track] gst error: {m.parse_error()}",
                                                       file=sys.stderr, flush=True), loop.quit()))
    bus.connect("message::eos", lambda b, m: loop.quit())
    pipe.set_state(Gst.State.PLAYING)
    print(f"[garuda-track] running: hef={hef} ipc={ipc_path} cmd={cmd_path}"
          + (f" view=http://0.0.0.0:{view_port}/" if view else " view=off"),
          file=sys.stderr, flush=True)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        pipe.set_state(Gst.State.NULL)
        svc.close()
        if view is not None:
            view.stop()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--hef", default="/usr/share/hailo-models/yolov8m_h10.hef")
    ap.add_argument("--ipc", default="/dev/shm/garuda_track.ipc")
    ap.add_argument("--cmd", default="/tmp/garuda_track.cmd")
    ap.add_argument("--min-hits", type=int, default=3)
    ap.add_argument("--n-coast", type=int, default=15)
    ap.add_argument("--view-port", type=int, default=8092, help="overlay+click MJPEG port (0=off)")
    a = ap.parse_args()
    run(a.hef, a.ipc, a.cmd, a.min_hits, a.n_coast, a.view_port)


if __name__ == "__main__":
    main()
