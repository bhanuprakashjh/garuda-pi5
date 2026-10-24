#!/usr/bin/env python3
"""ATE RMSE of an ORB-SLAM3 EuRoC trajectory vs the sequence groundtruth.
SE(3) Umeyama alignment (NO scale — stereo is metric), timestamp-associated within 20 ms.
    python3 datasets/euroc_ate.py <est_tum.txt> <mav0/state_groundtruth_estimate0/data.csv>
"""
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


et, ex = load_tum(sys.argv[1])
gt, gx = load_gt(sys.argv[2])
if len(et) < 10:
    print(f"ATE: est trajectory has only {len(et)} poses — tracking FAILED"); sys.exit(0)

ME, MG = [], []
for i in range(len(et)):
    j = int(np.argmin(np.abs(gt - et[i])))
    if abs(gt[j] - et[i]) < 20e6:
        ME.append(ex[i]); MG.append(gx[j])
E, G = np.array(ME), np.array(MG)
if len(E) < 10:
    print(f"ATE: only {len(E)} matched poses — tracking FAILED"); sys.exit(0)

muE, muG = E.mean(0), G.mean(0)
Ec, Gc = E - muE, G - muG
H = Ec.T @ Gc / len(E)
U, S, Vt = np.linalg.svd(H)
Dd = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
R = Vt.T @ Dd @ U.T
t = muG - R @ muE
err = np.linalg.norm((R @ E.T).T + t - G, axis=1)
plen = float(np.sum(np.linalg.norm(np.diff(G, axis=0), axis=1)))
print(f"ATE_RMSE={np.sqrt((err**2).mean())*100:.2f}cm mean={err.mean()*100:.2f}cm "
      f"max={err.max()*100:.2f}cm matched={len(E)}/{len(et)} pathlen={plen:.1f}m")
