"""Live stereo + IMU events from the OAK-D Pro for vio_feed.py. EXPERIMENTAL, bench only.

Opens the camera itself (one process may open the device: stop `garuda-oak-preview` first). Same
device settings as bench/oak_vio_record.py: raw 8-bit global-shutter mono images, raw
accelerometer and gyro reports, IR projector and flood light at zero, nothing computed on the camera.
"""
import contextlib
import time

SOCKETS = ("left", "CAM_B"), ("right", "CAM_C")


def factory_calibration(dai, np, cal, width, height):
    """The same fields the recorder writes to calibration.json (minus the delivered-image K, added later)."""
    left, right = (getattr(dai.CameraBoardSocket, s) for _, s in SOCKETS)
    out = {"width": width, "height": height,
           "left_to_right_m": np.array(cal.getCameraExtrinsics(left, right, False, dai.LengthUnit.METER)).tolist()}
    try:
        out["imu_to_left"] = np.array(cal.getImuToCameraExtrinsics(left, False, dai.LengthUnit.METER)).tolist()
        out["imu_to_left_unit"] = "m"
    except TypeError:
        out["imu_to_left"] = np.array(cal.getImuToCameraExtrinsics(left, False)).tolist()
        out["imu_to_left_unit"] = "sdk-default (centimetres per DepthAI documentation; check against factory dump)"
    return out


@contextlib.contextmanager
def open_camera(args, width=640, height=400):
    """Yields (calibration, events). events: ("accel"|"gyro", t_ns, xyz) and
    ("left"|"right", t_ns, image, captured_ns) where captured_ns is the exposure on the host's
    monotonic clock, as reported by DepthAI."""
    import depthai as dai
    import numpy as np
    with dai.Device() as dev:
        dev.setIrLaserDotProjectorIntensity(0)
        dev.setIrFloodLightIntensity(0)
        calibration = factory_calibration(dai, np, dev.readCalibration(), width, height)
        with dai.Pipeline(dev) as pipeline:
            queues = {}
            for name, socket in SOCKETS:
                cam = pipeline.create(dai.node.Camera).build(getattr(dai.CameraBoardSocket, socket))
                if args.max_exposure_us > 0:
                    cam.initialControl.setAutoExposureLimit(args.max_exposure_us)
                stream = cam.requestOutput((width, height), type=dai.ImgFrame.Type.GRAY8, fps=args.fps, enableUndistortion=False)
                queues[name] = stream.createOutputQueue(maxSize=8, blocking=False)   # live: old frames are worth nothing
            imu = pipeline.create(dai.node.IMU)
            imu.enableIMUSensor([dai.IMUSensor.ACCELEROMETER_RAW, dai.IMUSensor.GYROSCOPE_RAW], args.imu_hz)
            imu.setBatchReportThreshold(5)
            imu.setMaxBatchReports(40)
            imu_queue = imu.out.createOutputQueue(maxSize=400, blocking=False)
            pipeline.start()
            first = {}
            deadline = time.monotonic() + 10
            while len(first) < 2:                            # intrinsics of the image actually delivered
                if time.monotonic() > deadline:
                    raise SystemExit("REFUSED: no image from both cameras within 10 s")
                for name, q in queues.items():
                    f = q.tryGet()
                    if f is not None and name not in first:
                        first[name] = f
                        t = f.getTransformation()
                        calibration[name + "_k"] = np.array(t.getIntrinsicMatrix(), dtype=float).tolist()
                        calibration[name + "_d"] = np.array(t.getDistortionCoefficients(), dtype=float).tolist()
                time.sleep(0.002)

            def events():
                last_seq = {}
                while pipeline.isRunning():
                    idle = True
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
                                last_seq[stream] = seq
                                yield stream, int(r.getTimestampDevice().total_seconds() * 1e9), (float(r.x), float(r.y), float(r.z))
                    for name, q in queues.items():
                        f = q.tryGet()
                        if f is None:
                            continue
                        idle = False
                        yield (name, int(f.getTimestampDevice().total_seconds() * 1e9), f.getCvFrame().copy(),
                               int(f.getTimestamp().total_seconds() * 1e9))
                    if idle:
                        time.sleep(0.001)
            yield calibration, events()
