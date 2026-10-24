#!/usr/bin/env python3
"""garuda-portal seed: live CM3 + on-NPU detection, streamed to a browser.

Runs ON the Pi, stdlib only. Spawns rpicam-vid (MJPEG + hailo postprocess),
re-serves frames as multipart/x-mixed-replace at http://<pi>:8080/.
Bench tool — the real garuda-portal service grows from this shape.
"""
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CAM_CMD = [
    "rpicam-vid", "--nopreview", "-t", "0",
    "--codec", "mjpeg", "--width", "1280", "--height", "720",
    "--framerate", "15",
    "--post-process-file", "/usr/share/rpi-camera-assets/hailo_yolov8_inference.json",
    "-o", "-",
]

PAGE = b"""<!doctype html><html><head><title>Garuda Bench Portal</title><style>
body{background:#101418;color:#dfe7ef;font-family:sans-serif;text-align:center;margin:0}
h1{font-size:1.1rem;font-weight:600;padding:.8rem;margin:0;color:#7fd1b9}
img{max-width:96vw;border:1px solid #2a3540;border-radius:6px}
p{color:#6b7a88;font-size:.8rem}</style></head><body>
<h1>GARUDA bench portal &mdash; CM3 live &middot; YOLOv8m on Hailo-10H</h1>
<img src="/stream" alt="live camera stream">
<p>1280&times;720 @15 fps &middot; MJPEG &middot; detection overlay on-NPU</p>
</body></html>"""

latest = None
last_frame_t = time.monotonic()
cond = threading.Condition()

# rpicam-vid can wedge silently on long runs (observed 2026-07-30: process
# alive, CSI reinit loop, zero frames). Watchdog exits so systemd restarts us.
STALL_EXIT_S = 15.0


def watchdog():
    while True:
        time.sleep(5)
        if time.monotonic() - last_frame_t > STALL_EXIT_S:
            os._exit(1)


def camera_reader():
    global latest, last_frame_t
    proc = subprocess.Popen(CAM_CMD, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    buf = b""
    while True:
        chunk = proc.stdout.read(65536)
        if not chunk:
            os._exit(1)          # camera process ended: restart the service
        buf += chunk
        while True:
            s = buf.find(b"\xff\xd8\xff")
            e = buf.find(b"\xff\xd9", s + 3) if s >= 0 else -1
            if s < 0 or e < 0:
                if s > 0:
                    buf = buf[s:]
                break
            frame, buf = buf[s:e + 2], buf[e + 2:]
            with cond:
                latest = frame
                last_frame_t = time.monotonic()
                cond.notify_all()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            last = None
            try:
                while True:
                    with cond:
                        cond.wait(timeout=5)
                        frame = latest
                    if frame is None or frame is last:
                        continue
                    last = frame
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                     b"Content-Length: %d\r\n\r\n" % len(frame))
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                return
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


def main():
    threading.Thread(target=camera_reader, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()
