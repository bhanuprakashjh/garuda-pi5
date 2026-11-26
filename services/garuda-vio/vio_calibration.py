"""Calibration for the experimental OAK visual-inertial odometry: what the solver is given, and where
each value came from.

Three sources, later ones override earlier ones:
  1. FACTORY     the camera's own EEPROM values, as saved by the recorder in `calibration.json`.
                 Camera intrinsics and stereo extrinsics are the factory calibration. The
                 IMU-to-camera pose is the factory (mechanical) value and is unverified. IMU noise
                 values are placeholders and the camera-to-IMU time offset is 0.
  2. --basalt-calib FILE   a complete Basalt calibration written by `basalt_calibrate` +
                 `basalt_calibrate_imu` from recordings of a calibration target. It carries its own
                 camera model, so images are then given to the solver RAW (not undistorted).
  3. --station FILE        a small JSON with individual measured values for one camera unit,
                 e.g. only the time offset or only the IMU noise. Keys in STATION_KEYS.

`resolve()` returns the Basalt calibration plus a provenance table, so every result file can say
which values were measured and which were assumed. Nothing here measures anything.
"""
import hashlib
import json
import math
from pathlib import Path

import numpy as np

# Placeholders (continuous-time std devs in Basalt's units). NOT measured for the BNO086.
IMU_PLACEHOLDER = {"accel_noise_std": [0.05] * 3, "gyro_noise_std": [0.002] * 3,
                   "accel_bias_std": [0.005] * 3, "gyro_bias_std": [0.0005] * 3}
STATION_SCHEMA = "garuda-vio-station-1"
# key -> (provenance group, number of values; None = not a flat list)
STATION_KEYS = {"T_imu_cam": ("imu_to_camera_pose", None), "cam_time_offset_ns": ("camera_time_offset", None),
                "accel_noise_std": ("imu_noise", 3), "gyro_noise_std": ("imu_noise", 3),
                "accel_bias_std": ("imu_noise", 3), "gyro_bias_std": ("imu_noise", 3),
                "calib_accel_bias": ("imu_intrinsics", 9), "calib_gyro_bias": ("imu_intrinsics", 12)}
POSE_KEYS = ("px", "py", "pz", "qx", "qy", "qz", "qw")
MAX_TIME_OFFSET_NS = 100_000_000                             # larger than this is a mistake, not a calibration


class CalibrationError(ValueError):
    pass


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


def pose_matrix(pose):
    """4x4 from a Basalt pose {px..qw}. Refuses a quaternion that is not a unit quaternion."""
    try:
        px, py, pz, x, y, z, w = (float(pose[k]) for k in POSE_KEYS)
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationError(f"pose needs numeric {POSE_KEYS}: {exc!r}") from None
    if not all(math.isfinite(v) for v in (px, py, pz, x, y, z, w)) or abs(x * x + y * y + z * z + w * w - 1.0) > 1e-3:
        raise CalibrationError("pose quaternion is not a unit quaternion")
    T = np.eye(4)
    T[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                 [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                 [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    T[:3, 3] = (px, py, pz)
    return T


def poses(calibration):
    """(T_imu_left, T_imu_right) as 4x4 in metres from the FACTORY values: each camera's pose in the IMU frame."""
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
    """Basalt calibration from the factory values: pinhole cameras (for images already undistorted
    with the delivered K), factory poses, placeholder IMU noise, zero time offset."""
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


def _supplied(path):
    data = Path(path).read_bytes()
    return f"supplied: {Path(path).name} sha256 {hashlib.sha256(data).hexdigest()[:12]}", data


def _numbers(value, count, name):
    if not isinstance(value, list) or len(value) != count or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                       and math.isfinite(v) for v in value):
        raise CalibrationError(f"{name} must be a list of {count} finite numbers")
    return [float(v) for v in value]


def geometry(body):
    """Baseline and right-camera position from a Basalt calibration body. Gross checks only."""
    T_l, T_r = (pose_matrix(p) for p in body["T_imu_cam"])
    right_in_left = np.linalg.inv(T_l) @ T_r
    return {"baseline_m": float(np.linalg.norm(right_in_left[:3, 3])),
            "right_camera_position_in_left_frame_m": [float(v) for v in right_in_left[:3, 3]],
            "imu_to_left_camera_distance_m": float(np.linalg.norm(T_l[:3, 3]))}


def resolve(calibration, imu_rate_hz, basalt_calib=None, station=None):
    """Assemble the calibration the solver gets.

    Returns {"basalt": <Basalt JSON>, "undistort": bool, "cam_time_offset_ns": int,
             "provenance": {group: text}, "warnings": [..]}.
    `undistort` tells the caller whether images must be undistorted to pinhole with the delivered K
    (factory path) or passed RAW (a supplied Basalt calibration has its own camera model).
    Basalt's estimator does NOT apply `cam_time_offset_ns` itself: the caller must add it to every
    image timestamp. Raises CalibrationError on anything inconsistent; nothing is silently ignored."""
    size = [int(calibration["width"]), int(calibration["height"])]
    warnings = []
    if basalt_calib is None:
        out = basalt_calibration(calibration, imu_rate_hz)
        body = out["value0"]
        prov = {"camera_intrinsics": "factory (delivered-image K and distortion; images undistorted to pinhole)",
                "stereo_extrinsics": "factory",
                "imu_to_camera_pose": "factory mechanical value, unverified",
                "camera_time_offset": "0 ns, not calibrated",
                "imu_noise": "placeholder, not measured for this sensor",
                "imu_intrinsics": "none (bias and scale corrections are zero)"}
        undistort = True
    else:
        tag, data = _supplied(basalt_calib)
        try:
            out = json.loads(data)
            body = out["value0"]
            cams = len(body["intrinsics"])
            if cams != 2 or len(body["T_imu_cam"]) != 2 or len(body["resolution"]) != 2:
                raise CalibrationError(f"expected a two-camera calibration, found {cams}")
            for k, (_, count) in STATION_KEYS.items():
                if count:
                    _numbers(body[k], count, k)
            int(body["cam_time_offset_ns"])
            for cam in body["intrinsics"]:                   # a calibration run that saw no target writes inf/nan here
                values = list(cam["intrinsics"].values())
                if not values or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
                    raise CalibrationError("camera intrinsics are not finite numbers: the calibration did not converge")
            for pose in body["T_imu_cam"]:
                pose_matrix(pose)
            if all(abs(float(body["T_imu_cam"][1][k]) - float(body["T_imu_cam"][0][k])) < 1e-9 for k in POSE_KEYS):
                raise CalibrationError("both cameras have the same pose: the stereo extrinsics were not calibrated")
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            raise CalibrationError(f"{basalt_calib} is not a Basalt calibration: {exc!r}") from None
        for res in body["resolution"]:
            if [int(v) for v in res] != size:
                raise CalibrationError(f"calibration is for {res}, the stream is {size}: record the calibration "
                                       "session at the same resolution")
        body["imu_update_rate"] = float(imu_rate_hz)
        prov = {k: tag for k in ("camera_intrinsics", "stereo_extrinsics", "imu_to_camera_pose", "camera_time_offset",
                                 "imu_noise", "imu_intrinsics")}
        undistort = False
    if station is not None:
        tag, data = _supplied(station)
        try:
            values = json.loads(data)
        except json.JSONDecodeError as exc:
            raise CalibrationError(f"{station} is not JSON: {exc}") from None
        if not isinstance(values, dict) or values.get("schema") != STATION_SCHEMA:
            raise CalibrationError(f'{station} must be an object with "schema": "{STATION_SCHEMA}"')
        notes = {"schema", "station", "camera_serial", "method", "date", "operator", "notes"}
        unknown = sorted(set(values) - set(STATION_KEYS) - notes)
        if unknown:
            raise CalibrationError(f"{station}: unknown keys {unknown}; allowed values are {sorted(STATION_KEYS)}")
        for k, (group, count) in STATION_KEYS.items():
            if k not in values:
                continue
            v = values[k]
            if k == "T_imu_cam":
                if not isinstance(v, list) or len(v) != 2:
                    raise CalibrationError("T_imu_cam must be two poses, left then right")
                for pose in v:
                    pose_matrix(pose)                        # raises on a malformed pose
                v = [{key: float(pose[key]) for key in POSE_KEYS} for pose in v]
            elif k == "cam_time_offset_ns":
                if isinstance(v, bool) or not isinstance(v, int):
                    raise CalibrationError("cam_time_offset_ns must be a whole number of nanoseconds")
            else:
                v = _numbers(v, count, k)
                if k.endswith("_std") and min(v) <= 0:
                    raise CalibrationError(f"{k} must be positive")
            body[k] = v
            prov[group] = tag
            if k == "T_imu_cam":
                prov["stereo_extrinsics"] = tag              # the two poses also fix the camera-to-camera transform
    offset = int(body["cam_time_offset_ns"])
    if abs(offset) > MAX_TIME_OFFSET_NS:
        raise CalibrationError(f"camera time offset {offset} ns is beyond {MAX_TIME_OFFSET_NS} ns: check the value and its unit")
    geo = geometry(body)
    if not 0.06 <= geo["baseline_m"] <= 0.09:
        warnings.append(f"stereo baseline {geo['baseline_m']:.4f} m is not near the OAK-D Pro's 7.5 cm")
    if geo["right_camera_position_in_left_frame_m"][0] <= 0:
        warnings.append("right camera is not on the +x side of the left camera: extrinsic convention suspect")
    if geo["imu_to_left_camera_distance_m"] > 0.15:
        warnings.append("IMU is more than 15 cm from the left camera: check units of the IMU pose")
    # Factory intrinsics and stereo extrinsics are a real calibration; the other four are not, until supplied.
    assumed = sorted(k for k in ("imu_to_camera_pose", "camera_time_offset", "imu_noise", "imu_intrinsics")
                     if not prov[k].startswith("supplied"))
    return {"basalt": out, "undistort": undistort, "cam_time_offset_ns": offset, "provenance": prov,
            "geometry": geo, "warnings": warnings, "assumed": assumed}
