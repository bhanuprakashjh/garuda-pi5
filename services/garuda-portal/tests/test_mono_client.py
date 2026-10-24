import json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mono_client import snapshot_to_detections, MonoClient
from events import EventBus

SNAP = {"v": 1, "tracks": [{"id": 3, "label": "person", "conf": 0.9,
                            "x": 10.0, "y": 20.0, "w": 30.0, "h": 40.0, "coasting": False}],
        "lock": {"id": 3, "class": "person", "coasting": False},
        "distance": {"raw_m": 2.5, "est_m": 2.4, "valid": True},
        "depth_engine": "y26d", "detector": {"mode": "primary", "prompt": []},
        "cal": {"n": 0, "fit": None}, "frame": {"width": 1280, "height": 720}, "age_ms": 50}

def test_adapter_maps_tracks_to_detection_dicts():
    # mono reports pixels; the DetectionSet contract (interfaces/garuda_interfaces)
    # and every garuda-follow consumer require normalized [0,1] — the adapter must
    # divide through by the snapshot's own frame size (1280x720 here).
    d = snapshot_to_detections(SNAP)
    assert d["frame"] == {"width": 1280, "height": 720}
    det = d["detections"][0]
    assert det["label"] == "person" and det["confidence"] == 0.9
    assert (det["x"], det["y"], det["w"], det["h"]) == (10.0 / 1280, 20.0 / 720, 30.0 / 1280, 40.0 / 720)
    assert isinstance(det["class_id"], int)

def test_adapter_normalizes_by_actual_frame_size():
    # A different frame size must produce a different (correctly scaled) fraction —
    # guards against a hardcoded/assumed resolution creeping back in.
    snap = dict(SNAP, frame={"width": 640, "height": 480})
    det = snapshot_to_detections(snap)["detections"][0]
    assert (det["x"], det["y"], det["w"], det["h"]) == (10.0 / 640, 20.0 / 480, 30.0 / 640, 40.0 / 480)

def test_adapter_missing_frame_falls_back_to_1x1_no_crash():
    snap = dict(SNAP, frame={})
    det = snapshot_to_detections(snap)["detections"][0]
    # width/height default to 1 (existing fallback), so division is a no-op, not a ZeroDivisionError
    assert (det["x"], det["y"], det["w"], det["h"]) == (10.0, 20.0, 30.0, 40.0)

def test_adapter_unknown_label_gets_minus_one():
    snap = dict(SNAP, tracks=[dict(SNAP["tracks"][0], label="pen")])
    assert snapshot_to_detections(snap)["detections"][0]["class_id"] == -1

def _sse_server(payloads):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for p in payloads:
                self.wfile.write(b"data: " + json.dumps(p).encode() + b"\n\n")
            time.sleep(0.5)   # hold the connection open briefly
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def test_client_publishes_detections_and_mono_events():
    srv = _sse_server([SNAP])
    bus = EventBus(); q = bus.subscribe()
    MonoClient(bus, f"http://127.0.0.1:{srv.server_address[1]}").start()
    kinds = {}
    deadline = time.time() + 3
    while time.time() < deadline and not {"detections", "mono"} <= set(kinds):
        try: ev = q.get(timeout=0.5)
        except Exception: continue
        kinds[ev["kind"]] = ev["data"]
    srv.shutdown()
    assert "mono" in kinds and kinds["mono"]["lock"]["id"] == 3
    assert "detections" in kinds and kinds["detections"]["detections"][0]["label"] == "person"
