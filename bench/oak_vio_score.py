#!/usr/bin/env python3
"""Score an odometry trajectory against TAPE-MEASURED stops. No scale is fitted, nothing is aligned.

Protocol this scores (docs/CHENNAI_OAK_MAPPING.md): marks on the floor or a table, a tape-measured
distance between them, the camera set down on each mark and left still for a few seconds, always
facing the same way. The camera's rests are found from the GYRO (independent of the estimator);
the estimated position at each rest is the median over that rest.

  bench/oak_vio_score.py --trajectory <run>/trajectory.txt --gyro <run>/gyro.csv \\
      --route A M B M A --taped A-B=2.000 --taped A-M=1.000

--gyro takes the feeder's gyro.csv, a converted dataset's mav0/imu0/data.csv, or a recorder
session's imu.jsonl. Reported per taped pair: estimated distance, error in m and %, for every pair
of visits. Reported per mark visited twice: how far apart the two estimates are (return error).
A route that does not match the rests found is refused, with the rests listed.
"""
import argparse
import itertools
import json
from pathlib import Path

import numpy as np


def load_trajectory(path):
    rows = [l.split() for l in Path(path).read_text().splitlines() if l.strip() and not l.startswith("#")]
    a = np.array([[float(v) for v in r[:4]] for r in rows])
    if len(a) < 10:
        raise SystemExit("REFUSED: trajectory has fewer than 10 poses")
    return a[:, 0], a[:, 1:4]                                # seconds, metres


def load_gyro(path):
    """(seconds, |gyro| rad/s) from gyro.csv, a EuRoC imu0/data.csv or a recorder imu.jsonl."""
    path = Path(path)
    if path.suffix == ".jsonl":
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        g = [(r["device_ns"], (r["x"] ** 2 + r["y"] ** 2 + r["z"] ** 2) ** 0.5) for r in rows if r["stream"] == "gyro"]
    else:
        g = []
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            v = [float(x) for x in line.split(",")]
            g.append((v[0], v[1]) if len(v) == 2 else (v[0], float(np.linalg.norm(v[1:4]))))
    if len(g) < 50:
        raise SystemExit("REFUSED: fewer than 50 gyro samples")
    a = np.array(sorted(g))
    return a[:, 0] / 1e9, a[:, 1]


def rests(t, w, threshold=0.05, min_s=2.0, smooth_s=0.3, gap_s=0.5):
    """[(start_s, end_s)] where the smoothed gyro magnitude stays under threshold for at least min_s.
    A hole in the data longer than gap_s ends a rest (what happened in the hole is unknown)."""
    dt = float(np.median(np.diff(t)))
    k = max(1, int(round(smooth_s / dt)) | 1)
    pad = np.pad(w, k // 2, mode="edge")
    smooth = np.array([np.median(pad[i:i + k]) for i in range(len(w))]) if k > 1 else w
    out, start = [], None

    def close(end):
        if start is not None and t[end] - t[start] >= min_s:
            out.append((float(t[start]), float(t[end])))
    for i in range(len(t)):
        if start is not None and (smooth[i] >= threshold or t[i] - t[i - 1] > gap_s):
            close(i - 1); start = None
        if start is None and smooth[i] < threshold:
            start = i
    close(len(t) - 1)
    return out


def rest_positions(t, p, intervals, trim_s=0.3):
    out = []
    for a, b in intervals:
        m = (t >= a + trim_s) & (t <= b - trim_s)
        if m.sum() < 3:
            out.append(None); continue
        centre = np.median(p[m], axis=0)
        out.append({"start_s": a, "end_s": b, "poses": int(m.sum()), "position_m": [float(v) for v in centre],
                    "wander_m": float(np.max(np.linalg.norm(p[m] - centre, axis=1)))})
    return out


def score(t, p, gt, gw, route, taped, threshold=0.05, min_rest_s=2.0):
    found = rest_positions(t, p, rests(gt, gw, threshold, min_rest_s))
    listing = [{"rest": i + 1, "from_s": round(a, 2), "to_s": round(b, 2), "has_poses": f is not None}
               for i, ((a, b), f) in enumerate(zip(rests(gt, gw, threshold, min_rest_s), found))]
    if any(f is None for f in found):
        return {"scored": False, "reason": "a rest has no poses: the trajectory and the gyro file are not from the same run, or use different clocks", "rests_found": listing}
    if len(found) != len(route):
        return {"scored": False, "reason": f"route names {len(route)} stops but {len(found)} rests were found", "rests_found": listing}
    marks = set(route)
    for (a, b) in taped:
        if a not in marks or b not in marks or a == b:
            return {"scored": False, "reason": f"taped pair {a}-{b} does not name two different stops of the route", "rests_found": listing}
    visits = {}
    for label, f in zip(route, found):
        visits.setdefault(label, []).append(f)
    pairs = []
    for (a, b), metres in taped.items():
        for i, j in itertools.product(range(len(visits[a])), range(len(visits[b]))):
            est = float(np.linalg.norm(np.subtract(visits[a][i]["position_m"], visits[b][j]["position_m"])))
            pairs.append({"pair": f"{a}{i + 1}-{b}{j + 1}", "taped_m": metres, "estimated_m": est,
                          "error_m": est - metres, "error_percent": 100.0 * (est - metres) / metres})
    returns = []
    for label, v in visits.items():
        for i, j in itertools.combinations(range(len(v)), 2):
            returns.append({"mark": label, "visits": [i + 1, j + 1],
                            "apart_m": float(np.linalg.norm(np.subtract(v[i]["position_m"], v[j]["position_m"])))})
    steps = np.linalg.norm(np.diff(p, axis=0), axis=1)
    errors = [abs(x["error_m"]) for x in pairs]
    return {"scored": True, "scale_fitted": False, "aligned": False, "route": route,
            "stops": [dict(f, mark=label) for label, f in zip(route, found)],
            "taped_pairs": pairs, "returns": returns,
            "worst_taped_error_m": max(errors) if errors else None,
            "worst_return_m": max((r["apart_m"] for r in returns), default=None),
            "largest_wander_at_rest_m": max(f["wander_m"] for f in found),
            "path_length_m": float(steps.sum()), "largest_single_step_m": float(steps.max()),
            "steps_over_2cm": int((steps > 0.02).sum()), "poses": int(len(p)),
            "limits": ["tape and mark placement error are not included: state them with the result",
                       "one run says nothing about repeatability: score an independent second take"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trajectory", required=True)
    ap.add_argument("--gyro", required=True)
    ap.add_argument("--route", nargs="+", required=True, help="stop names in the order visited, e.g. A M B M A")
    ap.add_argument("--taped", action="append", default=[], metavar="A-B=METRES", help="tape-measured distance between two stops")
    ap.add_argument("--rest-threshold", type=float, default=0.05, help="gyro magnitude below this is rest, rad/s")
    ap.add_argument("--min-rest-s", type=float, default=2.0)
    ap.add_argument("--out", help="also write the result here as JSON")
    a = ap.parse_args(argv)
    taped = {}
    for item in a.taped:
        try:
            names, metres = item.split("=")
            x, y = names.split("-")
            taped[(x, y)] = float(metres)
            if not taped[(x, y)] > 0:
                raise ValueError
        except ValueError:
            ap.error(f"--taped {item!r}: expected A-B=METRES with a positive distance")
    if not taped:
        ap.error("at least one --taped distance is needed: without a tape there is nothing to score against")
    t, p = load_trajectory(a.trajectory)
    gt, gw = load_gyro(a.gyro)
    result = score(t, p, gt, gw, a.route, taped, a.rest_threshold, a.min_rest_s)
    text = json.dumps(result, indent=1)
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    return 0 if result["scored"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
