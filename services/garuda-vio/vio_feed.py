#!/usr/bin/env python3
"""Feed stereo images and IMU samples to the Basalt runner and collect its poses. EXPERIMENTAL.

  replay   --replay <session recorded by bench/oak_vio_record.py>   (no camera needed; add
           --realtime to pace it like the camera, otherwise it runs as fast as the solver takes it)
  live     --camera   opens the OAK-D Pro itself: `garuda-oak-preview` must be stopped for the
           session (one process may open the device), so the camera demo is off while this runs.

Output directory (new):
  poses.jsonl      one line per estimated state: time, position, orientation, velocity, biases, delay
  trajectory.txt   TUM format: t[s] x y z qx qy qz qw   (pose of the IMU in the odometry frame)
  summary.json     counts, dropped frames, delay percentiles, calibration provenance, temperature
  gyro.csv         gyro magnitude at about 50 Hz, for finding the rests when scoring (bench/oak_vio_score.py)

The pose is an ODOMETRY estimate: origin at the start, gravity-aligned, drifting. It is written to
local files for handheld bench inspection.
Calibration: factory values unless --basalt-calib / --station are given (see vio_calibration.py).
"""
import argparse
import heapq
import json
import struct
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

import vio_calibration

MAGIC = b"GVIO1\n"
PAIR_LIMIT_NS = 2_000_000
STOP_TEMP_C = 80.0


def imu_record(t_ns, gyro, accel):
    return b"I" + struct.pack("<q6d", int(t_ns), *gyro, *accel)


def frame_record(t_ns, images):
    h, w = images[0].shape
    for image in images:
        if image.dtype != np.uint8 or image.shape != (h, w):
            raise ValueError("frames must be 8-bit grey images of one size")
    return b"F" + struct.pack("<qHHB", int(t_ns), w, h, len(images)) + b"".join(np.ascontiguousarray(i).tobytes() for i in images)


class ImuMerger:
    """Gyro samples are the time base; accelerometer values are linearly interpolated onto them.

    Streaming form of bench/oak_vio_convert.py:merge_imu (same output for the same input): a gyro
    sample is released once an accelerometer sample at or after its time has arrived. Samples whose
    time does not increase are dropped and counted; nothing is extrapolated."""

    def __init__(self, history=16):
        self.gyro, self.accel, self.history = [], [], history   # pending gyro; recent accepted accel
        self.last = {"gyro": None, "accel": None}
        self.dropped = {"gyro": 0, "accel": 0, "gyro_outside_accel_span": 0}

    def add(self, stream, t_ns, xyz):
        """Returns the merged samples this report releases: [(t_ns, gyro xyz, accel xyz)]."""
        if self.last[stream] is not None and t_ns <= self.last[stream] or not all(np.isfinite(xyz)):
            self.dropped[stream] += 1
            return []
        self.last[stream] = t_ns
        if stream == "gyro":
            self.gyro.append((t_ns, tuple(xyz)))
        else:
            self.accel = (self.accel + [(t_ns, tuple(xyz))])[-self.history:]
        return self._release()

    def _release(self):
        out = []
        while self.gyro and self.accel and self.gyro[0][0] <= self.accel[-1][0]:
            t, g = self.gyro.pop(0)
            if t < self.accel[0][0]:                         # before the oldest accelerometer sample still held
                self.dropped["gyro_outside_accel_span"] += 1
                continue
            j = max(i for i, a in enumerate(self.accel) if a[0] <= t)
            a0 = self.accel[j]
            if t == a0[0]:
                out.append((t, g, a0[1]))
            else:
                a1 = self.accel[j + 1]
                u = (t - a0[0]) / (a1[0] - a0[0])
                out.append((t, g, tuple(a0[1][k] + u * (a1[1][k] - a0[1][k]) for k in range(3))))
        return out


class StereoPairer:
    """Pairs left and right frames by device time. Each frame is used once; unpaired ones are counted."""

    def __init__(self, limit_ns=PAIR_LIMIT_NS):
        self.limit, self.pending, self.unpaired = limit_ns, {"left": [], "right": []}, 0

    def add(self, stream, t_ns, item):
        """Returns (left_t_ns, left_item, right_item) when this frame completes a pair, else None."""
        other = "right" if stream == "left" else "left"
        waiting = self.pending[other]
        while waiting and waiting[0][0] < t_ns - self.limit:
            waiting.pop(0); self.unpaired += 1               # its mate can no longer arrive
        if waiting and abs(waiting[0][0] - t_ns) <= self.limit:
            t_other, mate = waiting.pop(0)
            return (t_ns, item, mate) if stream == "left" else (t_other, mate, item)
        self.pending[stream].append((t_ns, item))
        if len(self.pending[stream]) > 8:
            self.pending[stream].pop(0); self.unpaired += 1
        return None


def replay_events(session, cv2):
    """Events of a recorded session in device-time order:
    ("accel"|"gyro", t_ns, (x, y, z)) and ("left"|"right", t_ns, image)."""
    session = Path(session)
    frames = [json.loads(l) for l in (session / "frames.jsonl").read_text().splitlines() if l.strip()]
    imu = [json.loads(l) for l in (session / "imu.jsonl").read_text().splitlines() if l.strip()]
    rows = heapq.merge(sorted(frames, key=lambda r: r["device_ns"]), sorted(imu, key=lambda r: r["device_ns"]),
                       key=lambda r: r["device_ns"])
    for r in rows:
        if r["stream"] in ("accel", "gyro"):
            yield r["stream"], r["device_ns"], (r["x"], r["y"], r["z"])
        else:
            image = cv2.imread(str(session / r["file"]), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise SystemExit("REFUSED: unreadable image " + r["file"])
            yield r["stream"], r["device_ns"], image


def percentiles(values):
    if not values:
        return None
    v = np.asarray(values, float)
    return {"median": float(np.median(v)), "p95": float(np.percentile(v, 95)), "max": float(v.max()), "count": int(v.size)}


class Session:
    """Owns the runner process. `imu()` and `pair()` send data; states come back on a thread."""

    def __init__(self, command, out, resolved, undistort_maps, cv2, clock=time.monotonic_ns):
        self.out, self.cv2, self.maps, self.clock = Path(out), cv2, undistort_maps, clock
        self.offset = resolved["cam_time_offset_ns"]
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        self.proc.stdin.write(MAGIC)
        self.sent, self.delays_ms, self.tracked, self.end = {}, [], [], None
        self.counts = {"pairs_sent": 0, "imu_sent": 0, "states": 0}
        self.first_state, self.last_state = None, None
        self.lock = threading.Lock()
        self._poses = (self.out / "poses.jsonl").open("w")
        self._tum = (self.out / "trajectory.txt").open("w")
        self._tum.write("# timestamp[s] tx ty tz qx qy qz qw   (IMU pose in the odometry frame)\n")
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def imu(self, t_ns, gyro, accel):
        self.proc.stdin.write(imu_record(t_ns, gyro, accel))
        self.counts["imu_sent"] += 1

    def pair(self, t_ns, left, right, captured_ns=None):
        """t_ns: device time of the left image. captured_ns: host monotonic time of the exposure when
        known (live), else the delay is measured from the moment the pair is handed to the solver."""
        if self.maps is not None:
            left, right = (self.cv2.remap(img, *m, self.cv2.INTER_LINEAR) for img, m in zip((left, right), self.maps))
        stamp = t_ns + self.offset                           # Basalt does not apply the camera time offset itself
        with self.lock:
            self.sent[stamp] = captured_ns if captured_ns is not None else self.clock()
            if len(self.sent) > 2000:
                self.sent.pop(next(iter(self.sent)))
        self.proc.stdin.write(frame_record(stamp, (left, right)))
        self.counts["pairs_sent"] += 1

    def _read(self):
        for line in self.proc.stdout:
            now = self.clock()
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue                                     # Basalt prints its own progress lines on stdout
            kind = m.get("type")
            if kind == "state":
                with self.lock:
                    t0 = self.sent.pop(m["t_ns"], None)
                m["delay_ms"] = None if t0 is None else (now - t0) / 1e6
                if m["delay_ms"] is not None:
                    self.delays_ms.append(m["delay_ms"])
                self.counts["states"] += 1
                self.first_state = self.first_state or m
                self.last_state = m
                self._poses.write(json.dumps(m) + "\n")
                self._tum.write("%.9f %.6f %.6f %.6f %.7f %.7f %.7f %.7f\n" % ((m["t_ns"] / 1e9,) + tuple(m["p"]) + tuple(m["q"])))
            elif kind == "tracked":
                self.tracked.append(m["n"][0] if m["n"] else 0)
            elif kind == "end":
                self.end = m

    def close(self, timeout=120):
        try:
            self.proc.stdin.write(b"E")
            self.proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            code = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill(); code = "killed after timeout"
        self._reader.join(10)
        self._poses.close(); self._tum.close()
        return code


def temperature():
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000.0
    except (OSError, ValueError):
        return None


def throttled():
    try:
        return subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2).stdout.strip()
    except Exception:
        return None


def undistort_maps(calibration, cv2):
    maps = []
    for name in ("left", "right"):
        K, D = np.array(calibration[name + "_k"], float), np.array(calibration[name + "_d"], float)
        maps.append(cv2.initUndistortRectifyMap(K, D, None, K, (calibration["width"], calibration["height"]), cv2.CV_32FC1))
    return maps


def run(args, cv2, events, calibration, session_cls=Session):
    """events: iterable of (stream, t_ns, payload[, captured_ns]). Returns the summary dict."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    resolved = vio_calibration.resolve(calibration, args.imu_hz, args.basalt_calib, args.station)
    (out / "basalt_calib.json").write_text(json.dumps(resolved["basalt"], indent=1))
    maps = undistort_maps(calibration, cv2) if resolved["undistort"] else None
    command = [args.runner, "--cam-calib", str(out / "basalt_calib.json"), "--config-path", args.config,
               "--max-backlog", str(args.max_backlog), "--realtime", "1" if args.max_backlog else "0",
               "--report-tracked", "1" if args.report_tracked else "0"]
    if args.num_threads:
        command += ["--num-threads", str(args.num_threads)]
    session = session_cls(command, out, resolved, maps, cv2)
    merger, pairer, gyro_log = ImuMerger(), StereoPairer(), []      # gyro magnitude at about 50 Hz, kept in memory
    start_temp = peak_temp = temperature()
    stopped, t_first, wall_start, next_check = "end of input", None, time.monotonic(), time.monotonic() + 2.0
    try:
        for event in events:
            stream, t_ns, payload = event[:3]
            if t_first is None:
                t_first = t_ns
            if args.seconds and (t_ns - t_first) / 1e9 > args.seconds:
                stopped = "time"; break
            if args.realtime:                                # replay paced like the camera
                wait = (t_ns - t_first) / 1e9 - (time.monotonic() - wall_start)
                if wait > 0:
                    time.sleep(wait)
            if stream in ("accel", "gyro"):
                for t, g, a in merger.add(stream, t_ns, payload):
                    session.imu(t, g, a)
                    if session.counts["imu_sent"] % 8 == 0:
                        gyro_log.append((t, float(np.linalg.norm(g))))
            else:
                pair = pairer.add(stream, t_ns, (payload, event[3] if len(event) > 3 else None))
                if pair:
                    t, (left, captured), (right, _) = pair
                    session.pair(t, left, right, captured)
            if time.monotonic() >= next_check:
                next_check += 2.0
                t = temperature()
                if t is not None:
                    peak_temp = max(peak_temp or t, t)
                    if t >= STOP_TEMP_C:
                        stopped = f"temperature {t:.1f} C"; break
                if session.proc.poll() is not None:
                    stopped = f"runner exited with {session.proc.returncode}"; break
    except BrokenPipeError:
        stopped = "runner closed its input"
    except KeyboardInterrupt:
        stopped = "interrupted"
    finally:
        code = session.close()
    wall = time.monotonic() - wall_start
    (out / "gyro.csv").write_text("#t_ns,gyro_norm_rad_s\n" + "".join(f"{t},{w:.5f}\n" for t, w in gyro_log))
    end = session.end or {}
    first, last = session.first_state, session.last_state
    summary = {"mode": args.mode, "stopped_by": stopped, "runner_exit": code, "wall_s": round(wall, 2),
               "counts": dict(session.counts, unpaired_frames=pairer.unpaired, imu_dropped=merger.dropped,
                              frames_dropped_by_runner=end.get("frames_dropped")),
               "states_per_pair": (session.counts["states"] / session.counts["pairs_sent"]) if session.counts["pairs_sent"] else None,
               "delay_ms": percentiles(session.delays_ms),
               "delay_measured_from": "camera exposure (host clock)" if args.mode == "camera" else "hand-over to the solver (replay)",
               "tracked_points_left": percentiles(session.tracked),
               "end_position_from_start_m": None if not first else float(np.linalg.norm(np.subtract(last["p"], first["p"]))),
               "temperature_c": {"start": start_temp, "peak": peak_temp, "end": temperature()}, "throttled_end": throttled(),
               "calibration": {k: resolved[k] for k in ("provenance", "assumed", "geometry", "warnings", "cam_time_offset_ns", "undistort")},
               "config": args.config, "max_backlog": args.max_backlog,
               "limits": ["odometry, not ground truth: no accuracy is claimed by this file",
                          "values listed under calibration.assumed were not measured for this camera unit"]}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--replay", help="recorded session directory")
    src.add_argument("--camera", action="store_true", help="open the OAK-D Pro (camera service must be stopped)")
    p.add_argument("--out", required=True, help="new output directory")
    p.add_argument("--runner", required=True, help="path to garuda_vio_live")
    p.add_argument("--config", required=True, help="Basalt VIO config JSON")
    p.add_argument("--basalt-calib", help="complete Basalt calibration for this camera unit (images then go in RAW)")
    p.add_argument("--station", help="station values JSON (time offset, IMU noise, IMU pose): see vio_calibration.py")
    p.add_argument("--seconds", type=float, default=0, help="stop after this much data (0 = all of a replay; required for --camera)")
    p.add_argument("--realtime", action="store_true", help="replay at the recorded pace")
    p.add_argument("--max-backlog", type=int, default=None,
                   help="frames allowed to wait for the solver before new ones are dropped "
                        "(default: 3 for --camera and --realtime, 0 = never drop otherwise)")
    p.add_argument("--report-tracked", action="store_true", help="also record tracked point counts (costs some time)")
    p.add_argument("--num-threads", type=int, default=0)
    p.add_argument("--fps", type=int, default=30, choices=(15, 20, 30))
    p.add_argument("--imu-hz", type=int, default=400)
    p.add_argument("--max-exposure-us", type=int, default=8000)
    args = p.parse_args(argv)
    args.mode = "camera" if args.camera else "replay"
    if args.max_backlog is None:
        args.max_backlog = 3 if (args.camera or args.realtime) else 0
    import cv2
    if args.camera:
        if not 5 <= args.seconds <= 3600:
            p.error("--camera needs --seconds between 5 and 3600")
        import oak_source
        with oak_source.open_camera(args) as (calibration, events):
            summary = run(args, cv2, events, calibration)
    else:
        calibration = json.loads((Path(args.replay) / "calibration.json").read_text())
        summary = run(args, cv2, replay_events(args.replay, cv2), calibration)
    print(json.dumps({k: summary[k] for k in ("mode", "stopped_by", "runner_exit", "wall_s", "counts", "delay_ms",
                                              "tracked_points_left", "end_position_from_start_m", "temperature_c", "throttled_end")}))
    print("calibration not measured for this unit:", ", ".join(summary["calibration"]["assumed"]) or "nothing")


if __name__ == "__main__":
    main()
