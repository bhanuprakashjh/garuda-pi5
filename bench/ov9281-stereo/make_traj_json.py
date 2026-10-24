#!/usr/bin/env python3
"""Build viewer JSON: est trajectory Umeyama-aligned to GT (SE3, no scale), decimated,
per-point error, and stats. Same alignment math as datasets/euroc_ate.py."""
import json
import sys

import numpy as np


def load_tum(p):
    ts, xyz = [], []
    for L in open(p):
        q = L.split()
        if len(q) < 8:
            continue
        ts.append(float(q[0])); xyz.append([float(q[1]), float(q[2]), float(q[3])])
    return np.array(ts), np.array(xyz)


def load_gt(p):
    ts, xyz = [], []
    for L in open(p):
        if L.startswith("#"):
            continue
        q = L.strip().split(",")
        if len(q) < 4:
            continue
        ts.append(float(q[0])); xyz.append([float(q[1]), float(q[2]), float(q[3])])
    return np.array(ts), np.array(xyz)


def umeyama_se3(E, G):
    muE, muG = E.mean(0), G.mean(0)
    Ec, Gc = E - muE, G - muG
    H = Ec.T @ Gc / len(E)
    U, S, Vt = np.linalg.svd(H)
    Dd = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ Dd @ U.T
    t = muG - R @ muE
    return R, t


def decimate(a, n):
    if len(a) <= n:
        return a
    idx = np.linspace(0, len(a) - 1, n).astype(int)
    return a[idx]


def build(est_p, gt_p, N=700):
    et, ex = load_tum(est_p)
    gt, gx = load_gt(gt_p)
    # associate est->gt within 20ms for alignment
    ME, MG = [], []
    for i in range(len(et)):
        j = int(np.argmin(np.abs(gt - et[i])))
        if abs(gt[j] - et[i]) < 20e6:
            ME.append(ex[i]); MG.append(gx[j])
    E, G = np.array(ME), np.array(MG)
    R, t = umeyama_se3(E, G)
    est_al = (R @ ex.T).T + t                 # full est in GT frame
    est_al_m = (R @ E.T).T + t                # matched est in GT frame
    err = np.linalg.norm(est_al_m - G, axis=1)
    # decimate for the web (keep shape); GT downsampled too
    est_d = decimate(est_al, N)
    gt_d = decimate(gx, N)
    err_d = decimate(err, N)
    return {
        "est": [[round(float(v), 4) for v in p] for p in est_d],
        "gt": [[round(float(v), 4) for v in p] for p in gt_d],
        "err": [round(float(e) * 100, 2) for e in err_d],  # cm
        "stats": {
            "poses": int(len(et)),
            "matched": int(len(E)),
            "ate_rmse_cm": round(float(np.sqrt((err ** 2).mean()) * 100), 2),
            "ate_mean_cm": round(float(err.mean() * 100), 2),
            "ate_max_cm": round(float(err.max() * 100), 2),
            "pathlen_m": round(float(np.sum(np.linalg.norm(np.diff(G, axis=0), axis=1))), 1),
        },
    }


G = sys.argv[1]
out = {
    "euroc": build(f"{G}/euroc_out/f_MH01.txt",
                   f"{G}/datasets/euroc/MH_01_easy/mav0/state_groundtruth_estimate0/data.csv"),
    "tumvi": build(f"{G}/euroc_out/f_room1.txt",
                   f"{G}/datasets/tumvi/dataset-room1_512_16/mav0/mocap0/data.csv"),
}
print(json.dumps(out))
