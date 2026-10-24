"""garuda-portal HTTP wiring. Routes only — logic lives in the other modules."""
from __future__ import annotations
import json, pathlib, select, socket, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import configd_client
from readiness import run_checks

STATIC = pathlib.Path(__file__).parent / "static"

def list_hef_models(models_dir="/usr/share/hailo-models") -> list:
    """Basenames of compiled Hailo-10H models. [] if the dir doesn't exist (dev box)."""
    p = pathlib.Path(models_dir)
    if not p.is_dir():
        return []
    return sorted(f.name for f in p.glob("*_h10.hef"))

def capture_still(stream, out_dir="/home/garuda/portal_stills") -> dict:
    """Save the stream's current frame to <out_dir>/<t_ns>.jpg. {"ok": False} if no frame yet."""
    frame = stream.latest
    if frame is None:
        return {"ok": False}
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # wall clock deliberately: filename is a human-facing artifact, not a message
    # timestamp (monotonic resets each boot).
    path = out / f"{time.time_ns()}.jpg"
    path.write_bytes(frame)
    return {"ok": True, "file": str(path)}

def service_action(body: dict, call=None) -> dict:
    """POST /api/actions/service passthrough to configd's unit.action op."""
    call = call or configd_client.call
    return call("unit.action", body)

def inference_action(body: dict, manager) -> dict:
    """POST /api/actions/inference {"action": "start"|"stop"} dispatch. The
    camera-ownership rule itself lives in InferenceManager; this just routes."""
    action = body.get("action")
    if action == "start":
        return manager.start()
    if action == "stop":
        return manager.stop()
    return {"ok": False, "error": f"unknown action {action!r}"}

def follow_action(body: dict, pipeline) -> dict:
    """POST /api/actions/follow {"track_id": int|null} dispatch. Pins (or,
    with null, releases) the FollowPipeline's target. Portal-internal state
    only — no configd whitelist involved, this never touches the FC."""
    pipeline.set_pinned(body.get("track_id"))
    return {"ok": True, "pinned_id": pipeline.pinned_id}

def follow_publisher_loop(bus, pipeline):
    """Background thread body: subscribe to the bus like an SSE client would,
    and turn every `detections` event into a `follow` event (tracks +
    FollowCommand) on the same bus. Runs FollowPipeline exactly once per
    detection frame, decoupled from InferenceManager's detection loop so its
    camera-ownership/child-exit tests stay untouched.

    Per-event try/except (matching InferenceManager._frame_loop/_detection_loop's
    pattern): the detection payload is a subprocess JSONL hop away and can
    drift from what FollowPipeline expects. FollowPipeline.on_detections
    already drops individually malformed *detections*, but a single bad
    *event* (e.g. a non-dict payload from a corrupted/rewritten schema) must
    still not kill this thread — that would silently and permanently stop
    follow-event publishing for the rest of the process with no signal. On
    exception: publish an `error` event (visible on the same SSE channel the
    UI already reads) and keep looping."""
    q = bus.subscribe()
    try:
        while True:
            ev = q.get()
            if ev["kind"] == "detections":
                try:
                    bus.publish("follow", pipeline.on_detections(ev["data"]))
                except Exception as e:
                    bus.publish("error", {"stage": "follow", "detail": str(e)})
    finally:
        bus.unsubscribe(q)

def config_get(call=None) -> dict:
    """GET /api/config passthrough to configd's config.get op.

    Mirrors readiness_report's configd-down handling: a socket-missing/refused
    configd (dev PC, or a bench restart window) must not raise and dump a
    journald traceback — return a graceful ok:false the UI already understands.
    """
    call = call or configd_client.call
    try:
        return call("config.get", {})
    except Exception:
        return {"ok": False, "detail": "configd unavailable"}

def config_set(body: dict, call=None) -> dict:
    """PUT /api/config passthrough to configd's config.set op. See config_get
    for why the configd call is guarded."""
    call = call or configd_client.call
    try:
        return call("config.set", body)
    except Exception:
        return {"ok": False, "detail": "configd unavailable"}

def readiness_report(health_fn=None, config_call=None) -> dict:
    """GET /api/readiness/report. Collects fresh health + config, runs readiness checks."""
    import probes
    if health_fn is None:
        health_fn = probes.collect_health
    if config_call is None:
        config_call = configd_client.call

    health = health_fn()

    # Configd may fail on dev PC (socket missing). Gracefully default to {}.
    try:
        config_result = config_call("config.get", {})
        config = config_result.get("result", {}) if config_result.get("ok") else {}
    except Exception:
        config = {}

    return run_checks(health, config)

def health_with_camera_mode(collect_health, get_mode) -> dict:
    """Wrap a health probe so its dict always carries the current camera_mode —
    without this, reloading the page (or the first SSE tick after connect)
    would show a stale/missing camera state until the next start/stop transition."""
    data = collect_health()
    data["camera_mode"] = get_mode()
    return data

def mjpeg_client_loop(wfile, conn, stream, max_wait=5.0):
    """MJPEG multipart sender with peer-disconnect detection.

    Loops writing JPEG frames. Exits cleanly if:
    - Stream frame arrives and writes (BrokenPipeError surfaces disconnect)
    - select() timeout tick detects peer closed via MSG_PEEK
    """
    last = None
    while True:
        # Wait for frame update OR timeout (both let us check peer)
        with stream.cond:
            stream.cond.wait(timeout=max_wait)
            frame = stream.latest

        # Check if peer closed without writing
        try:
            ready, _, _ = select.select([conn], [], [], 0)
            if ready:
                # Peer readable = data or closed; MSG_PEEK tells us which
                data = conn.recv(1, socket.MSG_PEEK)
                if not data:
                    # Peer closed
                    return
        except OSError:
            # Socket error = peer gone
            return

        # Write frame if new
        if frame is not None and frame is not last:
            last = frame
            try:
                wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                           b"Content-Length: %d\r\n\r\n" % len(frame))
                wfile.write(frame)
                wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                return

def stream_handler_for(inference, camera_stream, max_wait=5.0):
    """Builds the /api/stream GET handler: raw camera feed in stream mode,
    annotated inference preview in inference mode (InferenceManager.active_source()
    picks) — same as before. The new part: when the source being served IS the
    raw CameraStream (not the inference preview), the CM3 is acquired for the
    lifetime of this one connection and released on disconnect
    (CameraStream.acquire()/release(), ref-counted so N concurrent viewers
    share one running rpicam-vid). This is what makes camera acquisition lazy
    end-to-end — nothing spawns rpicam-vid until a client actually opens the
    camera stream, so other single-client CM3 consumers (garuda-mono) are
    never starved just because the portal process is up."""
    def handler(h):
        h.send_response(200)
        h.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        h.end_headers()
        source = inference.active_source()
        owns_camera = source is camera_stream
        if owns_camera:
            camera_stream.acquire()
        try:
            mjpeg_client_loop(h.wfile, h.connection, source, max_wait=max_wait)
        finally:
            if owns_camera:
                camera_stream.release()
    return handler

def sse_client_loop(wfile, q, bus_unsub, timeout_s=5.0):
    """SSE event sender with keep-alive and graceful unsubscribe.

    Loops reading events and writing SSE: data: lines.
    On queue.Empty timeout, writes SSE comment ": ka\\n\\n" keep-alive.
    Exits on write errors.
    """
    import queue as queue_module
    try:
        while True:
            try:
                ev = q.get(timeout=timeout_s)
            except queue_module.Empty:
                # No event for 5s: send SSE keep-alive comment
                try:
                    wfile.write(b": ka\n\n")
                    wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                continue

            # Write event as SSE data line
            try:
                msg = b"data: " + json.dumps(ev).encode() + b"\n\n"
                wfile.write(msg)
                wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
    finally:
        bus_unsub()

def build_server(port: int, deps: dict) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path_base = self.path.split("?")[0]
            if path_base == "/api/health":
                return self._json(deps["health"]())
            if path_base.startswith("/api/"):
                extra = deps.get("get_routes", {}).get(path_base)
                if extra: return extra(self)
                return self._json({"error": "not found"}, 404)
            name = "index.html" if self.path == "/" else self.path.lstrip("/")
            f = (STATIC / name)
            if f.is_file() and STATIC in f.resolve().parents:
                ctype = {"html": "text/html", "js": "text/javascript",
                         "css": "text/css"}.get(f.suffix[1:], "application/octet-stream")
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            path_base = self.path.split("?")[0]
            fn = deps.get("post_routes", {}).get(path_base)
            if not fn: return self._json({"error": "not found"}, 404)
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n < 0:
                    raise ValueError("negative Content-Length")
            except (TypeError, ValueError):
                return self._json({"error": "bad request"}, 400)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._json({"error": "bad request"}, 400)
            return fn(self, body)

        def do_PUT(self): self.do_POST()
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)

def main():
    import threading
    import probes
    from events import EventBus
    from camera_stream import CameraStream
    from inference_manager import InferenceManager
    from calibration import CalibSession
    from follow_pipeline import FollowPipeline

    bus = EventBus()
    stream = CameraStream()   # lazy: acquired per-viewer by stream_handler_for(), not started here
    inference = InferenceManager(stream, bus)
    calib_session = CalibSession()
    follow_pipeline = FollowPipeline()

    import os
    from mono_client import MonoClient
    from gesture_actions import GestureReactor
    mono_url = os.environ.get("GARUDA_MONO_URL", "http://127.0.0.1:8098")
    MonoClient(bus, mono_url).start()
    GestureReactor(bus, follow_pipeline, mono_url).start()

    def events_handler(handler):
        """SSE event stream handler."""
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()
        q = bus.subscribe()
        sse_client_loop(handler.wfile, q, lambda: bus.unsubscribe(q))

    def models_handler(handler):
        handler._json(list_hef_models())

    def logs_handler(handler):
        body = probes.collect_logs().encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def service_action_handler(handler, body):
        handler._json(service_action(body))

    def capture_still_handler(handler, body):
        handler._json(capture_still(stream))

    def inference_action_handler(handler, body):
        handler._json(inference_action(body, inference))

    def follow_action_handler(handler, body):
        handler._json(follow_action(body, follow_pipeline))

    def health():
        return health_with_camera_mode(probes.collect_health, inference.get_mode)

    def health_publisher():
        """Background thread: publish health events every 2 seconds."""
        while True:
            time.sleep(2)
            bus.publish("health", health())

    def config_get_handler(handler):
        handler._json(config_get())

    def config_set_handler(handler, body):
        handler._json(config_set(body))

    def readiness_handler(handler):
        handler._json(readiness_report())

    def calib_start_handler(handler, body):
        pattern = tuple(body.get("pattern", [9, 6]))
        square_size_mm = body.get("square_size_mm", 25.0)
        calib_session.reset(pattern=pattern, square_size_m=square_size_mm / 1000.0)
        handler._json(calib_session.status())

    def calib_capture_handler(handler, body):
        handler._json(calib_session.capture(stream))

    def calib_solve_handler(handler, body):
        sensor = body.get("sensor", "imx708.cam0")
        handler._json(calib_session.solve_and_save(sensor))

    def calib_status_handler(handler):
        handler._json(calib_session.status())

    # Build deps dict with routes
    deps = {
        "health": health,
        "camera_mode": inference.get_mode,
        "get_routes": {
            "/api/stream": stream_handler_for(inference, stream),
            "/api/events": events_handler,
            "/api/models": models_handler,
            "/api/logs": logs_handler,
            "/api/config": config_get_handler,
            "/api/readiness/report": readiness_handler,
            "/api/calibration/status": calib_status_handler,
        },
        "post_routes": {
            "/api/actions/service": service_action_handler,
            "/api/actions/capture-still": capture_still_handler,
            "/api/actions/inference": inference_action_handler,
            "/api/actions/follow": follow_action_handler,
            "/api/config": config_set_handler,
            "/api/actions/calib-start": calib_start_handler,
            "/api/actions/calib-capture": calib_capture_handler,
            "/api/actions/calib-solve": calib_solve_handler,
        },
    }

    # Start background health publisher and the follow-event bridge
    threading.Thread(target=health_publisher, daemon=True).start()
    threading.Thread(target=follow_publisher_loop, args=(bus, follow_pipeline), daemon=True).start()

    server = build_server(8080, deps)
    server.serve_forever()

if __name__ == "__main__":
    main()
