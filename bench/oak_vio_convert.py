#!/usr/bin/env python3
"""Turn an oak_vio_record.py session into a EuRoC-layout dataset for offline Basalt.

--for-calibration keeps raw PNGs for the calibration GUIs. --basalt-calib uses the
same raw geometry with an external stereo/IMU solution. The following describes
the default factory-baseline path.

EXPERIMENTAL, offline, PC side. What it does, and what it assumes:
  images    left/right frames are paired by device time (hardware-synchronised sensors), each
            pair gets the LEFT timestamp; unpaired frames are dropped and counted. Images are
            undistorted with the delivered-image K and distortion and written as plain pinhole
            images with the same K (no rectification: the stereo extrinsics go to the solver).
  IMU       gyro samples are the time base; accelerometer values are linearly interpolated onto
            them (the BNO086's raw accelerometer arrives unevenly spaced on this bench).
  geometry  T_imu_cam (camera pose in the IMU frame) from the FACTORY values. Unverified.
  noise     IMU noise and bias values are PLACEHOLDERS, not measured for this sensor.
  time      camera-to-IMU time offset is written as 0. It is not calibrated.
The report says what was dropped and prints three sanity checks (baseline, which side the right
camera is on, where gravity points in the left camera at rest). It never claims accuracy.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile

import numpy as np

PAIR_LIMIT_NS = 2_000_000
# Placeholders (continuous-time std devs in Basalt's units). NOT measured for the BNO086.
IMU_PLACEHOLDER = {"accel_noise_std": [0.05] * 3, "gyro_noise_std": [0.002] * 3,
                   "accel_bias_std": [0.005] * 3, "gyro_bias_std": [0.0005] * 3}


def pair_frames(rows, limit_ns=PAIR_LIMIT_NS):
    """[(left_row, right_row)] in time order; each frame used once; and the number left over."""
    left = sorted((r for r in rows if r["stream"] == "left"), key=lambda r: r["device_ns"])
    right = sorted((r for r in rows if r["stream"] == "right"), key=lambda r: r["device_ns"])
    pairs, j, last = [], 0, None
    for l in left:
        while j < len(right) and right[j]["device_ns"] < l["device_ns"] - limit_ns:
            j += 1
        if j < len(right) and abs(right[j]["device_ns"] - l["device_ns"]) <= limit_ns and l["device_ns"] != last:
            pairs.append((l, right[j])); last = l["device_ns"]; j += 1
    return pairs, len(left) + len(right) - 2 * len(pairs)


def merge_imu(rows):
    """[(t_ns, wx, wy, wz, ax, ay, az)] at gyro times, accel interpolated; plus dropped counts.
    Samples whose time does not increase are dropped (a counter reset or repeated report)."""
    def clean(stream):
        out, dropped = [], 0
        for r in (r for r in rows if r["stream"] == stream):
            if out and r["device_ns"] <= out[-1][0] or not all(math.isfinite(r[k]) for k in "xyz"):
                dropped += 1
            else:
                out.append((r["device_ns"], r["x"], r["y"], r["z"]))
        return out, dropped
    gyro, gyro_dropped = clean("gyro")
    accel, accel_dropped = clean("accel")
    merged, j, outside = [], 0, 0
    for t, wx, wy, wz in gyro:
        while j + 1 < len(accel) and accel[j + 1][0] <= t:
            j += 1
        if not accel or t < accel[0][0] or j + 1 >= len(accel) and t != accel[-1][0]:
            outside += 1
            continue
        a0 = accel[j]
        if t == a0[0]:
            a = a0[1:]
        else:
            a1 = accel[j + 1]
            u = (t - a0[0]) / (a1[0] - a0[0])
            a = tuple(a0[k] + u * (a1[k] - a0[k]) for k in (1, 2, 3))
        merged.append((t, wx, wy, wz) + tuple(a))
    return merged, {"gyro_dropped": gyro_dropped, "accel_dropped": accel_dropped, "gyro_outside_accel_span": outside}


def quaternion(R):
    """(qx, qy, qz, qw) of a rotation matrix."""
    R = np.asarray(R, float)
    t = np.trace(R)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2; q = ((R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, 0.25 * s)
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2; q = (0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s, (R[2, 1] - R[1, 2]) / s)
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2; q = ((R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s, (R[0, 2] - R[2, 0]) / s)
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2; q = ((R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s, (R[1, 0] - R[0, 1]) / s)
    return tuple(float(v) for v in q)


def poses(calibration):
    """(T_imu_left, T_imu_right) as 4x4 in metres: the pose of each camera in the IMU frame."""
    unit = calibration["imu_to_left_unit"]
    imu_to_left = np.array(calibration["imu_to_left"], float)
    if unit != "m":
        imu_to_left[:3, 3] /= 100.0                          # recorded as centimetres
    left_to_right = np.array(calibration["left_to_right_m"], float)
    for name, T in (("imu_to_left", imu_to_left), ("left_to_right", left_to_right)):
        R = T[:3, :3]
        if T.shape != (4, 4) or not np.allclose(R @ R.T, np.eye(3), atol=1e-4) or abs(np.linalg.det(R) - 1) > 1e-4:
            raise ValueError(name + " is not a rigid transform")
    T_imu_left = np.linalg.inv(imu_to_left)                  # p_imu = T_imu_left * p_left
    T_imu_right = T_imu_left @ np.linalg.inv(left_to_right)  # p_left = inv(left_to_right) * p_right
    return T_imu_left, T_imu_right


def basalt_calibration(calibration, imu_rate_hz):
    T_l, T_r = poses(calibration)

    def pose(T):
        q = quaternion(T[:3, :3])
        return {"px": float(T[0, 3]), "py": float(T[1, 3]), "pz": float(T[2, 3]), "qx": q[0], "qy": q[1], "qz": q[2], "qw": q[3]}

    def pinhole(K):
        return {"camera_type": "pinhole", "intrinsics": {"fx": K[0][0], "fy": K[1][1], "cx": K[0][2], "cy": K[1][2]}}
    size = [calibration["width"], calibration["height"]]
    flat = {"value0": 0, "value1": 10000000000, "value2": [[1.0]] * 4}
    identity = {"px": 0.0, "py": 0.0, "pz": 0.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0}
    return {"value0": dict({"T_imu_cam": [pose(T_l), pose(T_r)],
                            "intrinsics": [pinhole(calibration["left_k"]), pinhole(calibration["right_k"])],
                            "resolution": [size, size], "vignette": [flat, flat],
                            "calib_accel_bias": [0.0] * 9, "calib_gyro_bias": [0.0] * 12,
                            "imu_update_rate": float(imu_rate_hz), "T_mocap_world": identity, "T_imu_marker": identity,
                            "mocap_time_offset_ns": 0, "mocap_to_imu_offset_ns": 0, "cam_time_offset_ns": 0}, **IMU_PLACEHOLDER)}


def sanity(calibration, imu, still_s=1.0):
    """Gross checks of the conventions. Not accuracy."""
    T_l, T_r = poses(calibration)
    right_in_left = np.linalg.inv(T_l) @ T_r
    out = {"baseline_m": float(np.linalg.norm(right_in_left[:3, 3])),
           "right_camera_position_in_left_frame_m": [float(v) for v in right_in_left[:3, 3]],
           "imu_to_left_camera_distance_m": float(np.linalg.norm(T_l[:3, 3]))}
    start = [r for r in imu if r[0] - imu[0][0] <= still_s * 1e9] if imu else []
    if start:
        a = np.mean([r[4:7] for r in start], axis=0)
        out["accel_norm_first_second_m_s2"] = float(np.linalg.norm(a))
        # A camera at rest measures +9.8 upward. Image y points down, so held level this is about (0, -9.8, 0).
        out["accel_first_second_in_left_camera_m_s2"] = [float(v) for v in T_l[:3, :3].T @ a]
        out["gyro_mean_first_second_rad_s"] = [float(v) for v in np.mean([r[1:4] for r in start], axis=0)]
    warn = []
    if not 0.06 <= out["baseline_m"] <= 0.09:
        warn.append("baseline is not near 7.5 cm")
    if out["right_camera_position_in_left_frame_m"][0] <= 0:
        warn.append("right camera is not on the +x side of the left camera: extrinsic convention suspect")
    if start and not 9.0 <= out["accel_norm_first_second_m_s2"] <= 10.6:
        warn.append("accelerometer does not read about 9.8 in the first second: was the camera still?")
    out["warnings"] = warn
    return out


def convert(session, out, cv2, *, for_calibration=False, basalt_calib=None):
    session, out = Path(session), Path(out)
    if for_calibration and basalt_calib is not None:
        raise ValueError("select either calibration capture or measured replay")
    if out.exists():
        raise FileExistsError(out)
    summary_file = session / "session.json"
    if summary_file.exists() and json.loads(summary_file.read_text()).get("complete") is False:
        raise ValueError("recording marked incomplete by recorder")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".oak-convert-") as tmp:
        staging = Path(tmp) / "dataset"
        report = _write_dataset(session, staging, cv2, for_calibration, basalt_calib)
        out.mkdir()  # Reserve the final name exclusively, even with concurrent conversions.
        staging.replace(out)
    return report


def _write_dataset(session, out, cv2, for_calibration, basalt_calib):
    calibration = json.loads((session / "calibration.json").read_text())
    size = [calibration["width"], calibration["height"]]
    raw = for_calibration or basalt_calib is not None
    offset = 0
    profile_hash = None
    if basalt_calib is not None:
        profile_bytes = Path(basalt_calib).read_bytes()
        profile_hash = hashlib.sha256(profile_bytes).hexdigest()
        profile = json.loads(profile_bytes)
        settings = profile["value0"]
        if settings.get("resolution") != [size, size]:
            raise ValueError(f"calibration must describe two raw cameras at {size}")
        if any(len(settings.get(key, [])) != 2 for key in ("intrinsics", "T_imu_cam")):
            raise ValueError("calibration needs left and right intrinsics and IMU poses")
        for key, length in (("accel_noise_std", 3), ("gyro_noise_std", 3),
                            ("accel_bias_std", 3), ("gyro_bias_std", 3),
                            ("calib_accel_bias", 9), ("calib_gyro_bias", 12)):
            values = np.asarray(settings.get(key), dtype=float)
            if values.shape != (length,) or not np.isfinite(values).all():
                raise ValueError(f"incomplete stereo/IMU calibration: {key}")
        for model, pose in zip(settings["intrinsics"], settings["T_imu_cam"]):
            params = model["intrinsics"]
            if (not model["camera_type"] or not np.isfinite(list(params.values())).all()
                    or min(params["fx"], params["fy"]) <= 0):
                raise ValueError("invalid camera intrinsics")
            translation = [pose[k] for k in ("px", "py", "pz")]
            rotation = [pose[k] for k in ("qx", "qy", "qz", "qw")]
            if not np.isfinite(translation).all() or not np.isclose(np.linalg.norm(rotation), 1, atol=1e-4):
                raise ValueError("invalid camera pose in IMU frame")
        offset = settings["cam_time_offset_ns"]
        if type(offset) is not int:
            raise ValueError("cam_time_offset_ns must be an integer")
        # Basalt calibration evaluates the spline at image time + offset. Apply it
        # to the image timeline once and keep the original calibration for provenance.
        settings["cam_time_offset_ns"] = 0
    frames = [json.loads(l) for l in (session / "frames.jsonl").read_text().splitlines() if l.strip()]
    imu_rows = [json.loads(l) for l in (session / "imu.jsonl").read_text().splitlines() if l.strip()]
    pairs, unpaired = pair_frames(frames)
    imu, imu_report = merge_imu(imu_rows)
    if len(pairs) < 10 or len(imu) < 100:
        raise SystemExit(f"REFUSED: too little data ({len(pairs)} pairs, {len(imu)} IMU samples)")
    if pairs[-1][0]["device_ns"] + offset <= imu[0][0] or pairs[0][0]["device_ns"] + offset >= imu[-1][0]:
        raise ValueError("camera and IMU times do not overlap")
    out.mkdir(parents=True, exist_ok=False)
    maps = {}
    for cam, name in enumerate(("left", "right")):
        (out / f"mav0/cam{cam}/data").mkdir(parents=True)
        if not raw:
            K, D = np.array(calibration[name + "_k"], float), np.array(calibration[name + "_d"], float)
            maps[name] = cv2.initUndistortRectifyMap(K, D, None, K, tuple(size), cv2.CV_32FC1)
    (out / "mav0/imu0").mkdir(parents=True)
    listing = ["#timestamp [ns],filename"]
    for l, r in pairs:
        stamp = l["device_ns"] + offset
        for cam, row, name in ((0, l, "left"), (1, r, "right")):
            image = cv2.imread(str(session / row["file"]), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise SystemExit("REFUSED: unreadable image " + row["file"])
            if list(image.shape[::-1]) != size:
                raise ValueError(f"recorded image dimensions disagree with calibration: {row['file']}")
            destination = out / f"mav0/cam{cam}/data/{stamp}.png"
            if raw:
                shutil.copyfile(session / row["file"], destination)
            elif not cv2.imwrite(str(destination), cv2.remap(image, *maps[name], cv2.INTER_LINEAR)):
                raise OSError(f"failed to write {destination}")
        listing.append(f"{stamp},{stamp}.png")
    for cam in (0, 1):
        (out / f"mav0/cam{cam}/data.csv").write_text("\n".join(listing) + "\n")
    with (out / "mav0/imu0/data.csv").open("w") as f:
        f.write("#timestamp [ns],w_RS_S_x [rad s^-1],w_RS_S_y [rad s^-1],w_RS_S_z [rad s^-1],a_RS_S_x [m s^-2],a_RS_S_y [m s^-2],a_RS_S_z [m s^-2]\n")
        for row in imu:
            f.write(str(row[0]) + "," + ",".join(repr(float(v)) for v in row[1:]) + "\n")
    span = (imu[-1][0] - imu[0][0]) / 1e9
    rate = (len(imu) - 1) / span
    if basalt_calib is not None:
        (out / "source_calibration.json").write_bytes(profile_bytes)
        (out / "basalt_calib.json").write_text(json.dumps(profile, indent=1, allow_nan=False))
    elif not for_calibration:
        (out / "basalt_calib.json").write_text(json.dumps(basalt_calibration(calibration, round(rate)), indent=1))
    skews = [abs(l["device_ns"] - r["device_ns"]) for l, r in pairs]
    steps = np.diff([l["device_ns"] for l, _ in pairs]) / 1e6
    report = {"pairs": len(pairs), "unpaired_frames_dropped": unpaired, "pair_skew_ns_max": int(max(skews)),
              "frame_interval_ms": {"median": float(np.median(steps)), "max": float(np.max(steps))},
              "imu_samples": len(imu), "imu_rate_hz": rate, "imu": imu_report, "duration_s": (pairs[-1][0]["device_ns"] - pairs[0][0]["device_ns"]) / 1e9,
              "pixel_geometry": "raw" if raw else "factory-undistorted",
              "camera_timestamp_shift_ns": offset,
              "source_calibration_sha256": profile_hash,
              "assumptions": ["factory camera and IMU extrinsics, unverified", "IMU noise values are placeholders, not measured",
                              "camera-to-IMU time offset written as 0, not calibrated", "images undistorted to pinhole with the delivered K"]}
    if raw:
        report["assumptions"] = ["cam0 is CAM_B (left), cam1 is CAM_C (right)",
                                 "the stereo pair shares the left device timestamp"]
    else:
        report["sanity"] = sanity(calibration, imu)
    (out / "conversion.json").write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session"); ap.add_argument("out")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--for-calibration", "--raw", action="store_true", help="export original stereo PNGs and IMU for the Basalt calibration tools")
    mode.add_argument("--basalt-calib", type=Path, help="replay original pixels with a Basalt raw stereo + IMU calibration for this unit and capture mode")
    a = ap.parse_args()
    import cv2
    print(json.dumps(convert(a.session, a.out, cv2, for_calibration=a.for_calibration, basalt_calib=a.basalt_calib), indent=1))
