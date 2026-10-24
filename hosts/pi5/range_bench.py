#!/usr/bin/env python3
"""Live accuracy + CPU harness for the stereo target-range feature (garuda-depth :8097).

Locks a COCO class, samples GET /api/target_range for a window, and reports the distance
distribution (median/min/max/std, hit-rate) plus garuda-depth CPU / SoC temp / throttle.
With --truth (a tape-measured distance in metres) it also prints the error. Idempotent:
clears the lock on exit so garuda-depth goes idle.

    # precision + CPU only (no ground truth):
    python3 hosts/pi5/range_bench.py --class chair --seconds 15
    # accuracy vs a tape measure (target physically placed at 2.00 m):
    python3 hosts/pi5/range_bench.py --class person --truth 2.00 --label 2m
"""
import argparse
import json
import statistics
import subprocess
import time
import urllib.request

BASE = "http://127.0.0.1:8097"


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.load(r)


def _post(path):
    req = urllib.request.Request(BASE + path, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def _busiest_python_cpu():
    """%CPU of the busiest python3 process (garuda-depth while ranging) via one top sample."""
    try:
        out = subprocess.run(["top", "-bn1"], capture_output=True, text=True, timeout=5).stdout
        best = 0.0
        for line in out.splitlines():
            parts = line.split()
            if len(parts) > 11 and parts[11].startswith("python3"):
                try:
                    best = max(best, float(parts[8]))
                except ValueError:
                    pass
        return f"{best:.0f}%"
    except Exception:
        return "?"


def _vcgen(cmd):
    try:
        return subprocess.run(["vcgencmd", cmd], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return "?"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--class", dest="cls", required=True, help="COCO class to lock (e.g. person, chair)")
    ap.add_argument("--truth", type=float, default=None, help="tape-measured distance (m) for error calc")
    ap.add_argument("--seconds", type=float, default=15.0, help="sampling window (default 15)")
    ap.add_argument("--label", default="", help="station label for the report line")
    args = ap.parse_args()

    _post(f"/api/range_target?class={args.cls}")
    time.sleep(1.0)                                   # detector load / first frames

    zs, counts, n = [], [], 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.seconds:
        n += 1
        try:
            d = _get("/api/target_range")
            if d.get("valid") and isinstance(d.get("z_m"), (int, float)):
                zs.append(float(d["z_m"]))
                counts.append(int(d.get("count", 1)))
        except Exception:
            pass
        time.sleep(0.25)

    cpu = _busiest_python_cpu()
    temp, throt = _vcgen("measure_temp"), _vcgen("get_throttled")
    _post("/api/range_target?class=")                # clear -> idle

    tag = f" [{args.label}]" if args.label else ""
    print(f"=== stereo target-range bench{tag}  class={args.cls} ===")
    print(f"samples={n}  hits={len(zs)} ({100 * len(zs) / max(n, 1):.0f}%)")
    if zs:
        med = statistics.median(zs)
        print(f"z_m: median={med:.3f}  min={min(zs):.3f}  max={max(zs):.3f}  "
              f"std={statistics.pstdev(zs):.3f} m  (n_valid={len(zs)}, count={max(counts)} max)")
        if args.truth:
            err = med - args.truth
            print(f"ground_truth={args.truth:.3f} m  error={err:+.3f} m ({100 * err / args.truth:+.1f}%)")
    else:
        print("no valid readings (target not detected / not ranged)")
    print(f"garuda-depth CPU={cpu}  {temp}  {throt}")


if __name__ == "__main__":
    main()
