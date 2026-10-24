#!/usr/bin/env python3
"""garuda-inference seed: live camera -> Hailo NPU -> structured DetectionSet JSONL.

Two output modes:
  default        — structured DetectionSets on stdout JSONL (topic "detections"),
                   for the drone/mapper. An empty detections list on a healthy
                   stream is a valid "nothing seen" observation, distinct from silence.
  --preview-mjpeg — the pipeline tees after the NPU postprocess: one branch keeps
                   emitting the structured DetectionSets (now on STDERR), the other
                   draws the boxes (hailooverlay) and JPEG-encodes, writing raw
                   concatenated JPEG frames to STDOUT. Lets the portal show an
                   annotated live preview AND keep the structured feed from one
                   camera holder. The structured stdout/stderr split keeps the
                   binary JPEG stream and the text JSONL on separate channels.

Runs ON the Pi. Requires: gstreamer1.0-libcamera, hailo-tappas-core (LGPL-dyn),
pygobject. Camera must be free (stop garuda-cam-portal first).

Usage: python3 garuda_inference.py [--hef PATH] [--preview-mjpeg]
"""
import argparse
import sys
import time

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402
import hailo  # noqa: E402  (tappas python binding)

sys.path.insert(0, "/home/garuda/garuda_pylib")  # bench deploy path for interfaces
from garuda_interfaces import (Detection, DetectionSet, FrameMeta,  # noqa: E402
                               JsonlPublisher)

POSTPROC = "/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/libyolo_hailortpp_post.so"

PIPELINE = (
    "libcamerasrc ! "
    "videoconvert ! videoscale ! video/x-raw,format=RGB,width=640,height=640 ! "
    "queue max-size-buffers=3 leaky=downstream ! "
    "hailonet hef-path={hef} ! "
    "queue max-size-buffers=3 leaky=downstream ! "
    "hailofilter so-path={post} qos=false ! "
    "appsink name=sink emit-signals=true max-buffers=2 drop=true sync=false"
)

# Preview: tee after hailofilter -> [detections appsink] + [draw -> jpeg appsink].
# hailooverlay draws the detections that hailofilter attached as ROI metadata.
PREVIEW_PIPELINE = (
    "libcamerasrc ! "
    "videoconvert ! videoscale ! video/x-raw,format=RGB,width=640,height=640 ! "
    "queue max-size-buffers=3 leaky=downstream ! "
    "hailonet hef-path={hef} ! "
    "queue max-size-buffers=3 leaky=downstream ! "
    "hailofilter so-path={post} qos=false ! "
    "tee name=t "
    "t. ! queue max-size-buffers=2 leaky=downstream ! "
    "appsink name=sink emit-signals=true max-buffers=2 drop=true sync=false "
    "t. ! queue max-size-buffers=2 leaky=downstream ! "
    "hailooverlay ! videoconvert ! jpegenc ! "
    "appsink name=mjpeg emit-signals=true max-buffers=2 drop=true sync=false"
)


class InferenceService:
    def __init__(self, hef: str, sensor: str, preview: bool = False):
        self.preview = preview
        # Preview mode: detections -> stderr (stdout carries the binary JPEG stream).
        self.publisher = JsonlPublisher(sys.stderr if preview else sys.stdout)
        self.model = hef.rsplit("/", 1)[-1]
        self.sensor = sensor
        self.frame_id = 0
        self.n_det = 0
        self.t_start = time.monotonic()
        Gst.init(None)
        tmpl = PREVIEW_PIPELINE if preview else PIPELINE
        self.pipeline = Gst.parse_launch(tmpl.format(hef=hef, post=POSTPROC))
        self.pipeline.get_by_name("sink").connect("new-sample", self.on_sample)
        if preview:
            self.pipeline.get_by_name("mjpeg").connect("new-sample", self.on_jpeg)

    def on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buf = sample.get_buffer()
        # buffer PTS is the pipeline timestamp; true sensor timestamps arrive with
        # the ICaptureBackend implementation (garuda-camera) — noted, not hidden.
        t_cap = buf.pts if buf.pts != Gst.CLOCK_TIME_NONE else time.monotonic_ns()
        self.frame_id += 1
        dets = []
        roi = hailo.get_roi_from_buffer(buf)
        for d in roi.get_objects_typed(hailo.HAILO_DETECTION):
            bbox = d.get_bbox()
            dets.append(Detection(
                class_id=d.get_class_id(), label=d.get_label(),
                confidence=round(d.get_confidence(), 4),
                x=round(bbox.xmin(), 4), y=round(bbox.ymin(), 4),
                w=round(bbox.width(), 4), h=round(bbox.height(), 4)))
        self.n_det += len(dets)
        msg = DetectionSet(
            frame=FrameMeta(frame_id=self.frame_id, t_capture_ns=t_cap,
                            clock_domain="mono", sensor=self.sensor,
                            width=640, height=640, pixel_format="RGB888"),
            detections=dets, t_infer_done_ns=time.monotonic_ns(), model=self.model)
        self.publisher.publish("detections", msg.to_dict())
        # fps log: only in default mode. In preview mode stderr carries the
        # detection JSONL, so a non-JSON log line there would break the reader.
        if not self.preview and self.frame_id % 150 == 0:
            el = time.monotonic() - self.t_start
            print(f"[garuda-inference] {self.frame_id} frames, "
                  f"{self.frame_id/el:.1f} fps, {self.n_det} detections total",
                  file=sys.stderr, flush=True)
        return Gst.FlowReturn.OK

    def on_jpeg(self, sink):
        """Preview branch: write each annotated JPEG frame to stdout as raw bytes.
        Frames are self-delimiting (SOI 0xFFD8 .. EOI 0xFFD9); the portal splits
        them with the same MJPEG parser it uses for the camera stream."""
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        ok, minfo = sample.get_buffer().map(Gst.MapFlags.READ)
        if ok:
            try:
                sys.stdout.buffer.write(minfo.data)
                sys.stdout.buffer.flush()
            except BrokenPipeError:
                pass  # portal disconnected; child will be killed on stop()
            finally:
                sample.get_buffer().unmap(minfo)
        return Gst.FlowReturn.OK

    def run(self):
        loop = GLib.MainLoop()
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", lambda b, m: (
            print(f"[garuda-inference] gst error: {m.parse_error()}",
                  file=sys.stderr, flush=True), loop.quit()))
        bus.connect("message::eos", lambda b, m: loop.quit())
        self.pipeline.set_state(Gst.State.PLAYING)
        try:
            loop.run()
        except KeyboardInterrupt:
            pass
        finally:
            self.pipeline.set_state(Gst.State.NULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hef", default="/usr/share/hailo-models/yolov8m_h10.hef")
    ap.add_argument("--sensor", default="imx708.cam0")
    ap.add_argument("--preview-mjpeg", action="store_true",
                    help="annotated JPEG frames on stdout, detections JSONL on stderr")
    args = ap.parse_args()
    InferenceService(args.hef, args.sensor, preview=args.preview_mjpeg).run()


if __name__ == "__main__":
    main()
