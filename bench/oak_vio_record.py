#!/usr/bin/env python3
"""Bounded stereo + IMU recording from the OAK-D Pro for OFFLINE visual-inertial experiments.

EXPERIMENTAL, bench only. Runs on the Pi in the isolated DepthAI venv. It opens the camera
itself, so `garuda-oak-preview` must be stopped for the session and started again afterwards
(one process may open the device). IR projector and flood light stay at zero. Nothing is
computed on the camera: it delivers the two raw global-shutter mono images and raw
accelerometer/gyro reports, each with the device clock timestamp. No flight-controller link.

Output (a NEW directory; images of the room, so keep it OUTSIDE Git):
  left/<seq>.png right/<seq>.png   raw 8-bit images, not undistorted, not rectified
  frames.jsonl                     stream, seq, device_ns, host_ns, exposure_us, file
  imu.jsonl                        stream (accel|gyro), seq, device_ns, x, y, z   (m/s^2, rad/s)
  calibration.json                 factory EEPROM dump, delivered-image K and distortion, extrinsics
  session.json                     settings, counts, sequence gaps, temperature, versions
Convert on the PC with bench/oak_vio_convert.py.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

MAX_SECONDS = 300
STOP_TEMP_C = 80.0


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


def exposure_us(frame):
    try:
        return int(frame.getExposureTime().total_seconds() * 1e6)
    except Exception:
        return None                                          # unknown is recorded as unknown


class FrameWriter:
    """Writes images on its own thread so a slow disk cannot stall the capture loop.

    The capture loop only copies a frame into memory. A stall of the SD card longer than the device
    queue (16 frames) used to lose frames AND IMU reports; with this the backlog absorbs it. The
    backlog is bounded: past `limit` frames put() blocks, and any loss then shows as sequence gaps."""

    def __init__(self, write, limit=900):
        import queue, threading
        self._q = queue.Queue(maxsize=limit)
        self._write, self.max_backlog, self.errors, self.blocked_s = write, 0, [], 0.0
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def put(self, path, image):
        import queue
        try:
            self._q.put_nowait((path, image))
        except queue.Full:                                   # backlog full: the caller waits, and that time is reported
            t0 = time.monotonic()
            self._q.put((path, image))
            self.blocked_s += time.monotonic() - t0
        self.max_backlog = max(self.max_backlog, self._q.qsize())

    def text(self, handle, chunk):
        """Append text to an open file from the writer thread (never from the capture loop)."""
        if chunk:
            self._q.put((handle, chunk, None))

    def _run(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            try:
                if len(item) == 3:                           # a chunk of log lines
                    item[0].write(item[1]); item[0].flush()
                elif self._write(*item) is False:            # cv2.imwrite reports failure by returning False
                    self.errors.append(f"{item[0]}: write returned False")
            except Exception as exc:                         # reported in session.json, never silent
                self.errors.append(f"{getattr(item[0], 'name', item[0])}: {exc!r}")

    def close(self):
        self._q.put(None)
        self._t.join()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="new output directory")
    p.add_argument("--seconds", type=float, default=120.0)
    p.add_argument("--fps", type=int, default=30, choices=(15, 20, 30))
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=400)
    p.add_argument("--imu-hz", type=int, default=400)
    p.add_argument("--max-exposure-us", type=int, default=8000,
                   help="upper limit for automatic exposure, to limit motion blur (0 = leave automatic exposure alone)")
    args = p.parse_args(argv)
    if not 5 <= args.seconds <= MAX_SECONDS:
        p.error(f"seconds must be 5..{MAX_SECONDS}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)                  # never record over an earlier session
    (out / "left").mkdir(); (out / "right").mkdir()
    import cv2                                               # imported late so --help works anywhere
    import depthai as dai
    import numpy as np

    sockets = {"left": dai.CameraBoardSocket.CAM_B, "right": dai.CameraBoardSocket.CAM_C}
    counts = {k: 0 for k in ("left", "right", "accel", "gyro")}
    last_seq, gaps = {}, {k: 0 for k in counts}
    exposure_limit, exposures = {}, []
    start_temp, peak_temp, stopped = temperature(), temperature(), "time"
    with dai.Device() as dev:
        dev.setIrLaserDotProjectorIntensity(0)
        dev.setIrFloodLightIntensity(0)
        cal = dev.readCalibration()
        calibration = {"factory": cal.eepromToJson(), "width": args.width, "height": args.height,
                       # p_right = left_to_right * p_left (metres), as used by hosts/pi5/oak/host_capture.py
                       "left_to_right_m": np.array(cal.getCameraExtrinsics(sockets["left"], sockets["right"], False,
                                                                            dai.LengthUnit.METER)).tolist()}
        try:                                                 # p_left = imu_to_left * p_imu; unit recorded, never assumed
            calibration["imu_to_left"] = np.array(cal.getImuToCameraExtrinsics(sockets["left"], False, dai.LengthUnit.METER)).tolist()
            calibration["imu_to_left_unit"] = "m"
        except TypeError:
            calibration["imu_to_left"] = np.array(cal.getImuToCameraExtrinsics(sockets["left"], False)).tolist()
            calibration["imu_to_left_unit"] = "sdk-default (centimetres per DepthAI documentation; check against factory dump)"
        with dai.Pipeline(dev) as pipeline:
            queues = {}
            for name, socket in sockets.items():
                cam = pipeline.create(dai.node.Camera).build(socket)
                if args.max_exposure_us > 0:                 # the result is checked afterwards from the recorded exposure
                    try:
                        cam.initialControl.setAutoExposureLimit(args.max_exposure_us)
                        exposure_limit[name] = "requested"
                    except Exception as exc:
                        exposure_limit[name] = "not applied: " + repr(exc)
                stream = cam.requestOutput((args.width, args.height), type=dai.ImgFrame.Type.GRAY8, fps=args.fps,
                                           enableUndistortion=False)
                queues[name] = stream.createOutputQueue(maxSize=90, blocking=False)
            imu = pipeline.create(dai.node.IMU)
            imu.enableIMUSensor([dai.IMUSensor.ACCELEROMETER_RAW, dai.IMUSensor.GYROSCOPE_RAW], args.imu_hz)
            imu.setBatchReportThreshold(5)
            imu.setMaxBatchReports(40)
            imu_queue = imu.out.createOutputQueue(maxSize=400, blocking=False)
            pipeline.start()
            writer = FrameWriter(lambda path, image: cv2.imwrite(path, image, [cv2.IMWRITE_PNG_COMPRESSION, 1]))
            longest_loop_s, loop_t = 0.0, time.monotonic()
            end = time.monotonic() + args.seconds
            next_check = time.monotonic() + 2.0
            # No file is written from this loop: while the card is busy with images the kernel holds up
            # every writer to it, and a held-up capture loop loses frames. Log lines are collected here
            # and handed to the writer thread twice a second, so an interrupted session keeps its
            # frames.jsonl / imu.jsonl up to the last half second.
            import signal

            def interrupt(*_):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, interrupt)         # systemd stop: finish the files like Ctrl-C
            lines, next_flush = {"frames": [], "imu": []}, time.monotonic() + 0.5
            with (out / "frames.jsonl").open("w") as frames, (out / "imu.jsonl").open("w") as imus:
              try:
                  def seen(stream, seq):
                      if stream in last_seq:
                          gaps[stream] += max(0, seq - last_seq[stream] - 1)
                      last_seq[stream] = seq
                      counts[stream] += 1
                  while pipeline.isRunning() and time.monotonic() < end:
                      now = time.monotonic()
                      longest_loop_s, loop_t = max(longest_loop_s, now - loop_t), now
                      idle = True
                      for name, q in queues.items():
                          f = q.tryGet()
                          if f is None:
                              continue
                          idle = False
                          seq = f.getSequenceNum()
                          if name + "_k" not in calibration:   # intrinsics of the image actually delivered
                              t = f.getTransformation()
                              calibration[name + "_k"] = np.array(t.getIntrinsicMatrix(), dtype=float).tolist()
                              calibration[name + "_d"] = np.array(t.getDistortionCoefficients(), dtype=float).tolist()
                          file = f"{name}/{seq:08d}.png"
                          writer.put(str(out / file), f.getCvFrame().copy())
                          seen(name, seq)
                          exposures.append(exposure_us(f))
                          lines["frames"].append(json.dumps({"stream": name, "seq": seq,
                                                             "device_ns": int(f.getTimestampDevice().total_seconds() * 1e9),
                                                             "host_ns": int(f.getTimestamp().total_seconds() * 1e9),
                                                             "exposure_us": exposures[-1],
                                                             "file": file}) + "\n")
                      for _ in range(8):
                          m = imu_queue.tryGet()
                          if m is None:
                              break
                          idle = False
                          for packet in m.packets:
                              for stream, r in (("accel", packet.acceleroMeter), ("gyro", packet.gyroscope)):
                                  seq = int(r.sequence)
                                  if last_seq.get(stream) == seq:
                                      continue                 # each packet repeats the other sensor's latest report
                                  seen(stream, seq)
                                  lines["imu"].append(json.dumps({"stream": stream, "seq": seq,
                                                                  "device_ns": int(r.getTimestampDevice().total_seconds() * 1e9),
                                                                  "x": float(r.x), "y": float(r.y), "z": float(r.z)}) + "\n")
                      if now >= next_flush:
                          next_flush = now + 0.5
                          writer.text(frames, "".join(lines["frames"])); writer.text(imus, "".join(lines["imu"]))
                          lines = {"frames": [], "imu": []}
                      if time.monotonic() >= next_check:
                          next_check += 2.0
                          t = temperature()
                          if t is not None:
                              peak_temp = max(peak_temp or t, t)
                              if t >= STOP_TEMP_C:
                                  stopped = f"temperature {t:.1f} C"
                                  break
                      if idle:
                          time.sleep(0.001)
              except KeyboardInterrupt:
                  stopped = "interrupted"                    # the summary below is still written
              finally:
                  writer.text(frames, "".join(lines["frames"])); writer.text(imus, "".join(lines["imu"]))
                  writer.close()                             # every queued image and line is on disk before the summary
        versions = {"depthai": dai.__version__, "opencv": cv2.__version__, "numpy": np.__version__}
    (out / "calibration.json").write_text(json.dumps(calibration))
    session = {"requested": vars(args), "stopped_by": stopped, "counts": counts, "sequence_gaps": gaps,
               "temperature_c": {"start": start_temp, "peak": peak_temp, "end": temperature()},
               "throttled_end": throttled(), "versions": versions,
               "writer": {"max_backlog_frames": writer.max_backlog, "errors": writer.errors[:5], "error_count": len(writer.errors),
                          "capture_blocked_ms": round(writer.blocked_s * 1e3, 1)},
               # False: an image listed in frames.jsonl is not on disk, or the session did not run to its end
               "complete": not writer.errors and stopped == "time",
               "longest_capture_loop_ms": round(longest_loop_s * 1e3, 1), "exposure_limit": exposure_limit,
               "exposure_us_max_observed": max((e for e in exposures if e is not None), default=None),
               "limits": ["exposure is automatic up to the requested limit: check exposure_us_max_observed", "camera-to-IMU time offset is not calibrated",
                          "IMU extrinsics are the factory values, unverified", "images are raw: not undistorted or rectified"]}
    (out / "session.json").write_text(json.dumps(session, indent=1))
    print(json.dumps({k: session[k] for k in ("stopped_by", "complete", "counts", "sequence_gaps", "writer", "longest_capture_loop_ms", "temperature_c", "throttled_end", "exposure_limit", "exposure_us_max_observed")}))


if __name__ == "__main__":
    main()
