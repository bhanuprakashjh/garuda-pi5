"""Offline checks of the taped-stop scorer. Synthetic trajectories; no camera, no solver."""
import json
import numpy as np
import pytest
from oak_vio_score import load_gyro, main, rests, score


def run(stops, scale=1.0, drift=0.0, rest_s=4.0, move_s=3.0, hz=30, gyro_hz=50):
    """Camera rests at each stop (x metres), moves between them. Returns t, p, gyro t, gyro |w|."""
    t, p, gt, gw, now = [], [], [], [], 100.0
    for k, x in enumerate(stops):
        for i in range(int(rest_s * hz)):
            t.append(now + i / hz); p.append((scale * x + drift * k, 0.001 * np.sin(i), 0.0))
        for i in range(int(rest_s * gyro_hz)):
            gt.append(now + i / gyro_hz); gw.append(0.004)
        now += rest_s
        if k + 1 < len(stops):
            for i in range(int(move_s * hz)):
                u = i / (move_s * hz)
                t.append(now + i / hz); p.append((scale * (x + u * (stops[k + 1] - x)), 0.05 * np.sin(6 * u), 0.0))
            for i in range(int(move_s * gyro_hz)):
                gt.append(now + i / gyro_hz); gw.append(0.6)
            now += move_s
    return np.array(t), np.array(p), np.array(gt), np.array(gw)


def test_rests_are_found_from_the_gyro_and_short_pauses_are_not_rests():
    t, p, gt, gw = run([0, 1, 2])
    found = rests(gt, gw)
    assert len(found) == 3 and found[0][0] == pytest.approx(100.0, abs=0.05) and found[1][1] - found[1][0] == pytest.approx(4.0, abs=0.1)
    gw[int(5.0 * 50):int(6.0 * 50)] = 0.004                  # a one-second pause while moving
    assert len(rests(gt, gw)) == 3


def test_an_estimate_that_is_five_percent_short_is_reported_as_such_with_no_scale_fitted():
    t, p, gt, gw = run([0, 1, 2, 1, 0], scale=0.95)
    r = score(t, p, gt, gw, list("AMBMA"), {("A", "B"): 2.0, ("A", "M"): 1.0})
    assert r["scored"] and r["scale_fitted"] is False
    ab = [x for x in r["taped_pairs"] if x["taped_m"] == 2.0]
    assert len(ab) == 2 and all(x["estimated_m"] == pytest.approx(1.9, abs=2e-3) and x["error_percent"] == pytest.approx(-5, abs=0.2) for x in ab)
    assert len([x for x in r["taped_pairs"] if x["taped_m"] == 1.0]) == 4      # two A visits x two M visits
    assert r["worst_taped_error_m"] == pytest.approx(0.1, abs=3e-3) and r["worst_return_m"] == pytest.approx(0, abs=3e-3)


def test_drift_shows_as_a_return_error_and_wander_at_rest_is_reported():
    t, p, gt, gw = run([0, 2, 0], drift=0.04)
    r = score(t, p, gt, gw, list("ABA"), {("A", "B"): 2.0})
    assert r["returns"] == [{"mark": "A", "visits": [1, 2], "apart_m": pytest.approx(0.08, abs=2e-3)}]
    assert r["largest_wander_at_rest_m"] < 0.003 and r["steps_over_2cm"] >= 0 and r["path_length_m"] > 4


def test_a_route_that_does_not_match_the_rests_is_refused_with_the_rests_listed():
    t, p, gt, gw = run([0, 2, 0])
    r = score(t, p, gt, gw, list("AB"), {("A", "B"): 2.0})
    assert not r["scored"] and "3 rests were found" in r["reason"] and len(r["rests_found"]) == 3
    assert not score(t, p, gt, gw, list("ABA"), {("A", "C"): 2.0})["scored"]
    assert "same run" in score(t + 5000, p, gt, gw, list("ABA"), {("A", "B"): 2.0})["reason"]


def test_command_line_reads_all_three_gyro_formats(tmp_path, capsys):
    t, p, gt, gw = run([0, 2, 0])
    traj = tmp_path / "trajectory.txt"
    traj.write_text("# header\n" + "".join("%.9f %.6f %.6f %.6f 0 0 0 1\n" % (a, *b) for a, b in zip(t, p)))
    feeder = tmp_path / "gyro.csv"; feeder.write_text("#t_ns,gyro_norm_rad_s\n" + "".join(f"{int(a * 1e9)},{w}\n" for a, w in zip(gt, gw)))
    euroc = tmp_path / "data.csv"; euroc.write_text("#timestamp\n" + "".join(f"{int(a * 1e9)},{w},0,0,0,0,9.8\n" for a, w in zip(gt, gw)))
    session = tmp_path / "imu.jsonl"
    session.write_text("".join(json.dumps({"stream": s, "seq": 0, "device_ns": int(a * 1e9), "x": w, "y": 0, "z": 0}) + "\n"
                               for a, w in zip(gt, gw) for s in ("gyro", "accel")))
    for f in (feeder, euroc, session):
        assert load_gyro(f)[1][0] == pytest.approx(0.004)
        assert main(["--trajectory", str(traj), "--gyro", str(f), "--route", "A", "B", "A", "--taped", "A-B=2.0", "--out", str(tmp_path / "s.json")]) == 0
    assert json.loads((tmp_path / "s.json").read_text())["taped_pairs"][0]["error_m"] == pytest.approx(0, abs=2e-3)
    assert main(["--trajectory", str(traj), "--gyro", str(feeder), "--route", "A", "B", "--taped", "A-B=2.0"]) == 1
    with pytest.raises(SystemExit):
        main(["--trajectory", str(traj), "--gyro", str(feeder), "--route", "A", "B", "A"])
