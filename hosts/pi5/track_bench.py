#!/usr/bin/env python3
"""Measure garuda-track on this Pi: run it for N seconds, sample front-end fps (from the service's
own log), process CPU%, SoC temp, and `vcgencmd get_throttled`. Prints a benchlog-ready block.

Assumes the camera + NPU are free (run after `run_track.sh` has stopped garuda-depth/portal, or
invoke this via run_track.sh's freeing then launch track_service yourself). Numbers are sacred:
this prints what it measured; nothing is estimated.
    python3 track_bench.py [seconds] [--view-port N]
"""
import re
import subprocess
import sys
import threading
import time

DIR = "/home/garuda/garuda-sdk-deploy/services/garuda-track"


def sh(cmd):
    return subprocess.getoutput(cmd).strip()


def main():
    dur = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 30
    view = "0"
    if "--view-port" in sys.argv:
        view = sys.argv[sys.argv.index("--view-port") + 1]

    p = subprocess.Popen([sys.executable, f"{DIR}/track_service.py", "--view-port", view],
                         cwd=DIR, stderr=subprocess.PIPE, text=True, bufsize=1)
    fps, temps, cpus, thr = [], [], [], set()
    t0 = time.monotonic()
    thr.add(sh("vcgencmd get_throttled"))

    def sampler():
        while time.monotonic() - t0 < dur and p.poll() is None:
            thr.add(sh("vcgencmd get_throttled"))
            mt = re.search(r"[\d.]+", sh("vcgencmd measure_temp"))
            if mt:
                temps.append(float(mt.group()))
            c = sh(f"ps -p {p.pid} -o %cpu=")
            if c:
                cpus.append(float(c))
            time.sleep(2)

    s = threading.Thread(target=sampler, daemon=True)
    s.start()
    while time.monotonic() - t0 < dur and p.poll() is None:
        line = p.stderr.readline()
        if not line:
            break
        m = re.search(r"([\d.]+) fps", line)
        if m:
            fps.append(float(m.group(1)))
    p.terminate()
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        p.kill()
    s.join(timeout=1)

    fps_med = sorted(fps)[len(fps) // 2] if fps else None
    print("=== garuda-track bench ===")
    print(f"duration: {dur}s   view-port: {view} (0=headless)")
    print(f"front-end fps: median {fps_med}  (samples {fps}) " if fps else "front-end fps: NO SAMPLES")
    print(f"process CPU%: median {sorted(cpus)[len(cpus)//2]:.0f}  peak {max(cpus):.0f}" if cpus else "CPU: n/a")
    print(f"SoC temp: peak {max(temps):.1f} C" if temps else "temp: n/a")
    print(f"get_throttled seen: {sorted(thr)}")


if __name__ == "__main__":
    main()
