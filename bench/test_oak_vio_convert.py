"""Offline checks of the OAK recording -> EuRoC/Basalt converter. Synthetic data, no camera."""
import json
from unittest.mock import patch
import numpy as np
import pytest
from oak_vio_convert import basalt_calibration, convert, merge_imu, pair_frames, poses, quaternion, sanity


def frame(stream, seq, ns):
    return {"stream": stream, "seq": seq, "device_ns": ns, "file": f"{stream}/{seq:08d}.png"}


def test_pairs_use_each_frame_once_and_drop_what_has_no_mate():
    rows = [frame("left", 1, 1000_000_000), frame("right", 1, 1000_100_000),          # 0.1 ms apart
            frame("left", 2, 1033_000_000),                                           # right frame lost
            frame("left", 3, 1066_000_000), frame("right", 3, 1069_000_000),          # 3 ms: too far
            frame("left", 4, 1100_000_000), frame("right", 4, 1100_000_000), frame("right", 5, 1100_500_000)]
    pairs, unpaired = pair_frames(rows)
    assert [(l["seq"], r["seq"]) for l, r in pairs] == [(1, 1), (4, 4)] and unpaired == 4


def imu(stream, ns, v):
    return {"stream": stream, "seq": 0, "device_ns": ns, "x": v, "y": 2 * v, "z": -v}


def test_accel_is_interpolated_onto_gyro_times_and_nothing_is_extrapolated():
    rows = [imu("accel", 0, 0.0), imu("accel", 4_000_000, 4.0), imu("accel", 6_000_000, 6.0),
            imu("gyro", -1_000_000, 9.0), imu("gyro", 1_000_000, 0.1), imu("gyro", 4_000_000, 0.2),
            imu("gyro", 5_000_000, 0.3), imu("gyro", 5_000_000, 0.4), imu("gyro", 7_000_000, 0.5)]
    merged, report = merge_imu(rows)
    assert [m[0] for m in merged] == [1_000_000, 4_000_000, 5_000_000]
    assert [round(m[4], 6) for m in merged] == [1.0, 4.0, 5.0] and round(merged[0][5], 6) == 2.0 and merged[2][1] == 0.3
    assert report == {"gyro_dropped": 1, "accel_dropped": 0, "gyro_outside_accel_span": 2}
    assert merge_imu([imu("gyro", 1, 0.1)])[0] == []                                  # no accelerometer at all


def rot_x(deg):
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def calibration(unit="m"):
    imu_to_left = np.eye(4); imu_to_left[:3, :3] = rot_x(90); imu_to_left[:3, 3] = [0.01, 0.02, 0.0]
    if unit != "m":
        imu_to_left[:3, 3] *= 100
    left_to_right = np.eye(4); left_to_right[0, 3] = -0.075                           # right camera 7.5 cm along +x of left
    k = [[450.0, 0, 320.0], [0, 450.0, 200.0], [0, 0, 1]]
    return {"width": 640, "height": 400, "imu_to_left": imu_to_left.tolist(), "imu_to_left_unit": unit,
            "left_to_right_m": left_to_right.tolist(), "left_k": k, "right_k": k, "left_d": [0.0] * 14, "right_d": [0.0] * 14}


def test_camera_poses_in_the_imu_frame_follow_the_recorded_conventions():
    T_l, T_r = poses(calibration())
    p_left = np.array([0.1, 0.2, 1.0, 1.0])
    assert np.allclose(np.array(calibration()["imu_to_left"]) @ (T_l @ p_left), p_left)          # T_imu_left inverts imu_to_left
    assert np.allclose((np.linalg.inv(T_l) @ T_r)[:3, 3], [0.075, 0, 0])                         # right camera sits at +x of left
    assert np.allclose(poses(calibration("cm"))[0], T_l)                                         # centimetres are converted
    bad = calibration(); bad["left_to_right_m"][0][0] = 2.0
    with pytest.raises(ValueError, match="rigid"):
        poses(bad)


def test_quaternion_round_trips_including_half_turns():
    for R in (np.eye(3), rot_x(90), rot_x(180), rot_x(-135), np.diag([-1.0, -1.0, 1.0]), np.diag([-1.0, 1.0, -1.0])):
        x, y, z, w = quaternion(R)
        back = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        assert np.allclose(back, R, atol=1e-9)


def test_calibration_file_has_basalts_fields_and_says_nothing_it_does_not_know():
    c = basalt_calibration(calibration(), 400)["value0"]
    assert [i["camera_type"] for i in c["intrinsics"]] == ["pinhole", "pinhole"] and c["resolution"] == [[640, 400]] * 2
    assert c["intrinsics"][0]["intrinsics"] == {"fx": 450.0, "fy": 450.0, "cx": 320.0, "cy": 200.0}
    assert c["cam_time_offset_ns"] == 0 and c["imu_update_rate"] == 400.0 and len(c["T_imu_cam"]) == 2
    assert c["calib_accel_bias"] == [0.0] * 9 and c["calib_gyro_bias"] == [0.0] * 12
    assert set(c) >= {"accel_noise_std", "gyro_noise_std", "accel_bias_std", "gyro_bias_std", "vignette", "T_mocap_world"}


def test_sanity_flags_a_flipped_baseline_and_a_moving_start():
    still = [(i * 2_500_000, 0, 0, 0, 0.0, 0.0, 9.81) for i in range(800)]             # IMU z up; camera rotated 90 deg about x
    s = sanity(calibration(), still)
    assert s["warnings"] == [] and abs(s["baseline_m"] - 0.075) < 1e-9
    assert np.allclose(s["accel_first_second_in_left_camera_m_s2"], [0, -9.81, 0], atol=1e-6)   # up is -y in the image frame
    flipped = calibration(); flipped["left_to_right_m"][0][3] = 0.075
    assert "convention suspect" in " ".join(sanity(flipped, still)["warnings"])
    assert "still" in " ".join(sanity(calibration(), [(r[0], 0, 0, 0, 3.0, 0, 0) for r in still])["warnings"])


def test_convert_writes_a_euroc_layout_and_refuses_thin_or_existing_output(tmp_path):
    cv2 = pytest.importorskip("cv2")
    session = tmp_path / "rec"; (session / "left").mkdir(parents=True); (session / "right").mkdir()
    rows = []
    for seq in range(12):
        for stream, skew in (("left", 0), ("right", 50_000)):
            row = frame(stream, seq, 5_000_000_000 + seq * 33_333_333 + skew)
            image = np.full((400, 640), 40 + seq, np.uint8); image[100:300, 200:400] = 200
            cv2.imwrite(str(session / row["file"]), image); rows.append(row)
    rows.append(frame("left", 99, 9_000_000_000)); cv2.imwrite(str(session / "left/00000099.png"), np.zeros((400, 640), np.uint8))
    (session / "frames.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    samples = []
    for i in range(300):
        t = 4_990_000_000 + i * 2_500_000
        samples += [dict(imu("gyro", t, 0.0), seq=i), {"stream": "accel", "seq": i, "device_ns": t + 700_000, "x": 0.0, "y": 0.0, "z": 9.81}]
    (session / "imu.jsonl").write_text("\n".join(json.dumps(r) for r in samples))
    (session / "calibration.json").write_text(json.dumps(calibration()))
    out = tmp_path / "euroc"
    report = convert(session, out, cv2)
    assert report["pairs"] == 12 and report["unpaired_frames_dropped"] == 1 and report["pair_skew_ns_max"] == 50_000
    assert abs(report["imu_rate_hz"] - 400) < 1 and report["sanity"]["warnings"] == []
    stamps = (out / "mav0/cam0/data.csv").read_text().splitlines()
    assert stamps[0] == "#timestamp [ns],filename" and stamps[1] == "5000000000,5000000000.png" and len(stamps) == 13
    assert (out / "mav0/cam1/data.csv").read_text() == (out / "mav0/cam0/data.csv").read_text()      # the pair shares one stamp
    assert cv2.imread(str(out / "mav0/cam1/data/5000000000.png"), 0).shape == (400, 640)
    first = (out / "mav0/imu0/data.csv").read_text().splitlines()[1].split(",")
    assert len(first) == 7 and float(first[6]) == 9.81
    assert json.loads((out / "basalt_calib.json").read_text())["value0"]["imu_update_rate"] == 400.0
    with pytest.raises(FileExistsError):
        convert(session, out, cv2)
    raw = tmp_path / "raw"
    convert(session, raw, cv2, for_calibration=True)
    assert (raw / "mav0/cam0/data/5000000000.png").read_bytes() == (session / rows[0]["file"]).read_bytes()
    assert not (raw / "basalt_calib.json").exists()

    # Synthetic timing offset exercises the native calibration-file interface.
    profile = basalt_calibration(calibration(), 400)
    profile["value0"]["cam_time_offset_ns"] = -800_000
    source = tmp_path / "measured.json"
    source.write_text(json.dumps(profile))
    replay = tmp_path / "replay"
    report = convert(session, replay, cv2, basalt_calib=source)
    assert report["camera_timestamp_shift_ns"] == -800_000
    assert (replay / "source_calibration.json").read_bytes() == source.read_bytes()
    assert (replay / "mav0/cam1/data/4999200000.png").read_bytes() == (session / rows[1]["file"]).read_bytes()
    assert (replay / "mav0/imu0/data.csv").read_bytes() == (raw / "mav0/imu0/data.csv").read_bytes()
    assert json.loads((replay / "basalt_calib.json").read_text())["value0"]["cam_time_offset_ns"] == 0
    profile["value0"]["resolution"][0] = [320, 200]
    source.write_text(json.dumps(profile))
    failed = tmp_path / "failed"
    with pytest.raises(ValueError, match="two raw cameras"):
        convert(session, failed, cv2, basalt_calib=source)
    assert not failed.exists()
    (session / "session.json").write_text('{"complete":false}')
    with pytest.raises(ValueError, match="incomplete"):
        convert(session, failed, cv2)
    (session / "session.json").write_text("{}")
    with patch.object(cv2, "imwrite", return_value=False):
        with pytest.raises(OSError, match="failed to write"):
            convert(session, failed, cv2)
    assert not failed.exists()
    (session / "frames.jsonl").write_text("\n".join(json.dumps(r) for r in rows[:6]))
    with pytest.raises(SystemExit, match="too little"):
        convert(session, tmp_path / "thin", cv2)
