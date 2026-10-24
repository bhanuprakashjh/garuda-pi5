#!/usr/bin/env python3
"""Live overlay + click-to-lock view for garuda-track.

Pure, testable pieces:
  draw_overlay(frame_bgr, tracked)  -> frame with per-track boxes; the locked target highlighted.
  display_to_frame(dx,dy, ...)      -> map a browser click (display px) to detection-frame px.

Server (bench): a threaded MJPEG HTTP server (the hosts/pi5/camarray_focus.py pattern). GET / serves a
page whose <img> streams /stream and whose click handler POSTs /lock?x&y; the handler maps the click to
frame coords and emits a LockCommand via the provided sink (garuda-track's UDS cmd channel). set_frame()
hands it the latest annotated JPEG from the pipeline callback.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# BGR colors
_LOCK = (231, 192, 63)      # cyan-ish accent (BGR)
_CONF = (140, 210, 140)     # confirmed track (green)
_TENT = (120, 120, 120)     # tentative (grey)


def draw_overlay(frame_bgr, tracked):
    """Draw track boxes on a BGR frame; the locked track gets a thick accent box + banner. Returns frame."""
    import cv2  # bench-only dep; kept out of module import so display_to_frame tests without it
    out = frame_bgr
    for o in tracked.objects:
        x, y, w, h = (int(round(v)) for v in o.bbox)
        locked = (o.track_id == tracked.locked_id)
        state = o.state.value if hasattr(o.state, "value") else str(o.state)
        col = _LOCK if locked else (_CONF if state == "confirmed" else _TENT)
        cv2.rectangle(out, (x, y), (x + w, y + h), col, 3 if locked else 1)
        tag = f"{'LOCK ' if locked else ''}#{o.track_id} {o.cls_name} {o.conf:.2f}"
        cv2.putText(out, tag, (x, max(12, y - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, col, 2 if locked else 1, cv2.LINE_AA)
    ls = tracked.lock_state.value if hasattr(tracked.lock_state, "value") else str(tracked.lock_state)
    cv2.putText(out, f"{ls}  tracks:{len(tracked.objects)}", (8, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, _LOCK, 2, cv2.LINE_AA)
    return out


def display_to_frame(dx, dy, disp_w, disp_h, frame_w, frame_h):
    """Map a click at (dx,dy) in a disp_w x disp_h rendered image to (fx,fy) in the frame_w x frame_h frame."""
    fx = int(round(dx * frame_w / max(1, disp_w)))
    fy = int(round(dy * frame_h / max(1, disp_h)))
    return (max(0, min(frame_w - 1, fx)), max(0, min(frame_h - 1, fy)))


_PAGE = """<!doctype html><meta charset=utf-8><title>garuda-track</title>
<style>body{{margin:0;background:#0b0f14;color:#cdd;font-family:system-ui}}
#w{{max-width:{w}px;margin:0 auto}} img{{width:100%;display:block;cursor:crosshair}}
button{{font:13px monospace;margin:6px;padding:6px 12px;background:#1a2630;color:#cde;border:1px solid #2a3a46;border-radius:6px;cursor:pointer}}</style>
<div id=w><img id=v src="/stream"><div><button onclick="fetch('/unlock',{{method:'POST'}})">Unlock</button>
<span>click a target to lock</span></div></div>
<script>const v=document.getElementById('v');
v.onclick=e=>{{const r=v.getBoundingClientRect();
fetch(`/lock?x=${{Math.round(e.clientX-r.left)}}&y=${{Math.round(e.clientY-r.top)}}&w=${{Math.round(r.width)}}&h=${{Math.round(r.height)}}`,{{method:'POST'}});}};
</script>"""


class ViewServer:
    """Threaded MJPEG + click server. on_lock(fx, fy) and on_unlock() are called for UI actions;
    frame_wh is the detection-frame size the clicks map into."""
    def __init__(self, port, frame_wh, on_lock, on_unlock):
        self.frame_w, self.frame_h = frame_wh
        self._jpeg = b""
        self._lock = threading.Lock()
        self.on_lock, self.on_unlock = on_lock, on_unlock
        view = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
                    self.end_headers()
                    try:
                        while True:
                            with view._lock:
                                j = view._jpeg
                            if j:
                                self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + j + b"\r\n")
                            threading.Event().wait(0.04)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                else:
                    body = _PAGE.format(w=view.frame_w).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def do_POST(self):
                q = parse_qs(urlparse(self.path).query)
                if self.path.startswith("/lock"):
                    dx = float(q.get("x", [0])[0]); dy = float(q.get("y", [0])[0])
                    dw = float(q.get("w", [view.frame_w])[0]); dh = float(q.get("h", [view.frame_h])[0])
                    fx, fy = display_to_frame(dx, dy, dw, dh, view.frame_w, view.frame_h)
                    view.on_lock(fx, fy)
                elif self.path.startswith("/unlock"):
                    view.on_unlock()
                self.send_response(204)
                self.end_headers()

        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), H)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def set_frame(self, jpeg_bytes):
        with self._lock:
            self._jpeg = jpeg_bytes

    def stop(self):
        self.httpd.shutdown()
