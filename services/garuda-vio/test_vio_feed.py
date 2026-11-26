"""Offline checks of the VIO feeder and calibration assembly. Synthetic data; no camera, no Basalt."""
import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench"))
import vio_calibration as vc
import vio_feed as vf
from oak_vio_convert import merge_imu, pair_frames

FACTORY = {"width": 640, "height": 400, "imu_to_left_unit": "m",
           "imu_to_left": [[1, 0, 0, 0.01], [0, 1, 0, 0.02], [0, 0, 1, 0.0], [0, 0, 0, 1]],
           "left_to_right_m": [[1, 0, 0, -0.075], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
           "left_k": [[450, 0, 320], [0, 450, 200], [0, 0, 1]], "right_k": [[451, 0, 321], [0, 451, 201], [0, 0, 1]],
           "left_d": [0.0] * 14, "right_d": [0.0] * 14}


def test_streaming_imu_merge_equals_the_batch_converter():
    rng = np.random.default_rng(1)
    rows, t = [], 0
    for i in range(400):                                     # gyro even, accel uneven, with a repeat and a NaN
        rows.append({"stream": "gyro", "seq": i, "device_ns": 2_500_000 * i + 700, "x": float(rng.normal()), "y": 0.1, "z": -0.2})
        t += int(rng.integers(1_000_000, 4_500_000))
        rows.append({"stream": "accel", "seq": i, "device_ns": t, "x": float(i), "y": 9.8, "z": float(rng.normal())})
    rows.append(dict(rows[-1])); rows.append({"stream": "gyro", "seq": 0, "device_ns": 10**9 + 5, "x": float("nan"), "y": 0, "z": 0})
    rows.sort(key=lambda r: r["device_ns"])
    batch, report = merge_imu(rows)
    m, out = vf.ImuMerger(), []
    for r in rows:
        out += m.add(r["stream"], r["device_ns"], (r["x"], r["y"], r["z"]))
    assert [(t, *g, *a) for t, g, a in out] == pytest.approx(batch)
    assert m.dropped["gyro"] == report["gyro_dropped"] and m.dropped["accel"] == report["accel_dropped"]
    assert len(out) > 300


def test_imu_merge_never_extrapolates_and_survives_a_late_gyro_report():
    m = vf.ImuMerger()
    assert m.add("gyro", 5, (1, 1, 1)) == []                 # no accelerometer yet
    assert m.add("accel", 10, (0, 0, 0)) == [] and m.dropped["gyro_outside_accel_span"] == 1
    assert m.add("accel", 20, (10, 0, 0)) == [] and m.add("accel", 30, (20, 0, 0)) == []
    assert m.add("gyro", 15, (2, 2, 2)) == [(15, (2, 2, 2), (5.0, 0.0, 0.0))]   # bracket found in the history
    assert m.add("gyro", 40, (3, 3, 3)) == []                # beyond the newest accelerometer sample: waits
    assert m.add("accel", 50, (40, 0, 0)) == [(40, (3, 3, 3), (30.0, 0.0, 0.0))]


def test_streaming_pairer_equals_the_batch_converter():
    rng = np.random.default_rng(2)
    rows = []
    for i in range(200):
        t = 33_333_333 * i
        if rng.random() > 0.1:
            rows.append({"stream": "left", "seq": i, "device_ns": t})
        if rng.random() > 0.1:
            rows.append({"stream": "right", "seq": i, "device_ns": t + int(rng.integers(-30_000, 30_000))})
    rows.sort(key=lambda r: r["device_ns"])
    batch, unpaired = pair_frames(rows)
    p, out = vf.StereoPairer(), []
    for r in rows:
        got = p.add(r["stream"], r["device_ns"], r["seq"])
        if got:
            out.append(got)
    assert [(t, l, r) for t, l, r in out] == [(l["device_ns"], l["seq"], r["seq"]) for l, r in batch]
    assert p.unpaired + sum(len(v) for v in p.pending.values()) == unpaired


def test_wire_records_match_what_the_runner_reads():
    rec = vf.imu_record(123, (1, 2, 3), (4, 5, 6))
    assert rec[:1] == b"I" and struct.unpack("<q6d", rec[1:]) == (123, 1, 2, 3, 4, 5, 6) and len(rec) == 57
    left = np.arange(12, dtype=np.uint8).reshape(3, 4); right = left + 100
    rec = vf.frame_record(77, (left, right))
    assert rec[:1] == b"F" and struct.unpack("<qHHB", rec[1:14]) == (77, 4, 3, 2)
    assert rec[14:26] == left.tobytes() and rec[26:] == right.tobytes()
    with pytest.raises(ValueError):
        vf.frame_record(1, (left, right.astype(np.uint16)))


def test_factory_calibration_says_what_is_not_measured():
    r = vc.resolve(FACTORY, 400)
    assert r["undistort"] and r["cam_time_offset_ns"] == 0 and r["warnings"] == []
    assert r["assumed"] == ["camera_time_offset", "imu_intrinsics", "imu_noise", "imu_to_camera_pose"]
    assert r["geometry"]["baseline_m"] == pytest.approx(0.075)
    assert r["basalt"]["value0"]["intrinsics"][0]["camera_type"] == "pinhole"


def station(tmp_path, **values):
    f = tmp_path / "station.json"
    f.write_text(json.dumps(dict({"schema": vc.STATION_SCHEMA, "station": "CHN-bench-01"}, **values)))
    return f


def test_station_values_override_and_are_named_in_the_provenance(tmp_path):
    f = station(tmp_path, cam_time_offset_ns=-3_200_000, gyro_noise_std=[0.001] * 3, accel_noise_std=[0.02] * 3,
                gyro_bias_std=[1e-4] * 3, accel_bias_std=[1e-3] * 3)
    r = vc.resolve(FACTORY, 400, station=f)
    body = r["basalt"]["value0"]
    assert r["cam_time_offset_ns"] == -3_200_000 == body["cam_time_offset_ns"] and body["gyro_noise_std"] == [0.001] * 3
    assert r["provenance"]["camera_time_offset"].startswith("supplied: station.json sha256 ")
    assert r["provenance"]["imu_noise"].startswith("supplied") and r["provenance"]["imu_to_camera_pose"].startswith("factory")
    assert r["assumed"] == ["imu_intrinsics", "imu_to_camera_pose"] and r["undistort"]


@pytest.mark.parametrize("values, message", [
    (dict(cam_time_offset_ns=3.2), "whole number"), (dict(cam_time_offset_ns=2 * 10**9), "beyond"),
    (dict(gyro_noise_std=[0.001, 0.001]), "list of 3"), (dict(accel_noise_std=[0.0, 0.1, 0.1]), "positive"),
    (dict(time_offset_ms=3), "unknown keys"), (dict(schema="other"), "schema"),
    (dict(T_imu_cam=[{"px": 0, "py": 0, "pz": 0, "qx": 0, "qy": 0, "qz": 0, "qw": 2}] * 2), "unit quaternion")])
def test_malformed_station_values_are_refused_not_ignored(tmp_path, values, message):
    with pytest.raises(vc.CalibrationError, match=message):
        vc.resolve(FACTORY, 400, station=station(tmp_path, **values))


def test_a_complete_basalt_calibration_is_used_raw_and_checked(tmp_path):
    full = vc.basalt_calibration(FACTORY, 400)
    full["value0"]["intrinsics"] = [{"camera_type": "ds", "intrinsics": {"fx": 450, "fy": 450, "cx": 320, "cy": 200, "xi": -0.2, "alpha": 0.5}}] * 2
    full["value0"]["cam_time_offset_ns"] = 1_500_000
    f = tmp_path / "result_calib.json"; f.write_text(json.dumps(full))
    r = vc.resolve(FACTORY, 400, basalt_calib=f)
    assert not r["undistort"] and r["cam_time_offset_ns"] == 1_500_000 and r["assumed"] == []
    assert r["basalt"]["value0"]["intrinsics"][0]["camera_type"] == "ds"
    full["value0"]["resolution"] = [[1280, 800]] * 2; f.write_text(json.dumps(full))
    with pytest.raises(vc.CalibrationError, match="same resolution"):
        vc.resolve(FACTORY, 400, basalt_calib=f)
    f.write_text("{}")
    with pytest.raises(vc.CalibrationError, match="not a Basalt calibration"):
        vc.resolve(FACTORY, 400, basalt_calib=f)
    # what basalt_calibrate writes when it never saw the target (seen on the bench 2026-11-26)
    full = vc.basalt_calibration(FACTORY, 400); full["value0"]["T_imu_cam"][1] = dict(full["value0"]["T_imu_cam"][0])
    f.write_text(json.dumps(full))
    with pytest.raises(vc.CalibrationError, match="same pose"):
        vc.resolve(FACTORY, 400, basalt_calib=f)
    full = vc.basalt_calibration(FACTORY, 400); full["value0"]["intrinsics"][1]["intrinsics"]["fx"] = float("inf")
    f.write_text(json.dumps(full))
    with pytest.raises(vc.CalibrationError, match="did not converge"):
        vc.resolve(FACTORY, 400, basalt_calib=f)


class FakeProc:
    def __init__(self, lines):
        self.stdin, self.stdout, self.returncode = self, lines, 0
        self.written = b""
    def write(self, b): self.written += b
    def close(self): pass
    def wait(self, timeout=None): return 0
    def poll(self): return None


def test_session_adds_the_time_offset_and_measures_delay(tmp_path, monkeypatch):
    clock = iter(range(1_000_000_000, 10**12, 5_000_000))    # 5 ms per reading: one for the chatter line, one for the state
    state = json.dumps({"type": "state", "t_ns": 1000 + 250, "p": [0, 0, 0], "q": [0, 0, 0, 1], "v": [0] * 3, "bg": [0] * 3, "ba": [0] * 3})
    end = json.dumps({"type": "end", "frames_in": 1, "frames_dropped": 0, "imu_in": 0, "states": 1})
    proc = FakeProc([b"basalt chatter\n", state.encode() + b"\n", end.encode() + b"\n"])
    hold = __import__("threading").Event()
    def answers(lines=proc.stdout):                          # the runner answers only after the pair was sent
        hold.wait(5)
        yield from lines
    proc.stdout = answers()
    monkeypatch.setattr(vf.subprocess, "Popen", lambda *a, **k: proc)
    s = vf.Session(["runner"], tmp_path, {"cam_time_offset_ns": 250}, None, None, clock=lambda: next(clock))
    img = np.zeros((2, 2), np.uint8)
    s.pair(1000, img, img)
    hold.set()
    assert s.close() == 0
    assert proc.written.startswith(vf.MAGIC + b"F" + struct.pack("<q", 1250)) and proc.written.endswith(b"E")
    assert s.counts["states"] == 1 and s.delays_ms == [10.0] and s.end["frames_in"] == 1
    assert (tmp_path / "trajectory.txt").read_text().splitlines()[1].startswith("0.000001250 0.000000")
