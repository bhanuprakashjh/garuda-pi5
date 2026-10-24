#!/usr/bin/env python3
"""Build a 3D room reconstruction from the OV9281 self-cal run: SGBM depth on each keyframe's
rectified stereo pair, reprojected to 3D and placed in the world by the ORB-SLAM3 keyframe pose.
Also emits the full camera trajectory. Output: /home/garuda/euroc_live/cloud.json"""
import glob
import json
import os

import cv2
import numpy as np

D = "/home/garuda/euroc_live/move1_fit"
OUT = "/home/garuda/euroc_live/mvout"
B = 0.047524

fs = cv2.FileStorage(f"{D}/cam.yaml", cv2.FILE_STORAGE_READ)
fx = fs.getNode("Camera1.fx").real(); fy = fs.getNode("Camera1.fy").real()
cx = fs.getNode("Camera1.cx").real(); cy = fs.getNode("Camera1.cy").real()
fs.release()
print(f"intrinsics fx={fx:.1f} fy={fy:.1f} cx={cx:.1f} cy={cy:.1f} B={B}")


def q2R(qx, qy, qz, qw):
    n = np.sqrt(qx*qx+qy*qy+qz*qz+qw*qw); qx, qy, qz, qw = qx/n, qy/n, qz/n, qw/n
    return np.array([
        [1-2*(qy*qy+qz*qz), 2*(qx*qy-qz*qw), 2*(qx*qz+qy*qw)],
        [2*(qx*qy+qz*qw), 1-2*(qx*qx+qz*qz), 2*(qy*qz-qx*qw)],
        [2*(qx*qz-qy*qw), 2*(qy*qz+qx*qw), 1-2*(qx*qx+qy*qy)]])


def load_tum(p):
    out = []
    for L in open(p):
        q = L.split()
        if len(q) >= 8:
            out.append([float(x) for x in q[:8]])
    return out


left = sorted(glob.glob(f"{D}/mav0/cam0/data/*.png"))   # cam0 dir = LEFT in move1_fit
right = sorted(glob.glob(f"{D}/mav0/cam1/data/*.png"))
lmap = {int(os.path.splitext(os.path.basename(f))[0]): f for f in left}
rmap = {int(os.path.splitext(os.path.basename(f))[0]): f for f in right}
ns = np.array(sorted(lmap))

sgbm = cv2.StereoSGBM_create(minDisparity=0, numDisparities=128, blockSize=5,
                             P1=8*25, P2=32*25, uniquenessRatio=10,
                             speckleWindowSize=100, speckleRange=2, disp12MaxDiff=1)

kfs = load_tum(f"{OUT}/kf_mv.txt")
print(f"keyframes: {len(kfs)}")
pts = []
for (ts, tx, ty, tz, qx, qy, qz, qw) in kfs:
    j = int(np.argmin(np.abs(ns - ts*1e9)))
    L = cv2.imread(lmap[ns[j]], 0); R = cv2.imread(rmap[ns[j]], 0)
    disp = sgbm.compute(L, R).astype(np.float32) / 16.0
    ys, xs = np.where(disp > 2.0)
    dd = disp[ys, xs]
    Z = fx * B / dd
    m = (Z > 0.2) & (Z < 6.0)
    ys, xs, Z = ys[m], xs[m], Z[m]
    X = (xs - cx) * Z / fx; Y = (ys - cy) * Z / fy
    Xc = np.stack([X, Y, Z], 1)
    g = L[ys, xs]
    if len(Xc) > 3500:
        idx = np.linspace(0, len(Xc)-1, 3500).astype(int)
        Xc, g = Xc[idx], g[idx]
    Xw = (q2R(qx, qy, qz, qw) @ Xc.T).T + np.array([tx, ty, tz])
    for p, gg in zip(Xw, g):
        pts.append([round(float(p[0]), 3), round(float(p[1]), 3), round(float(p[2]), 3), int(gg)])

# full trajectory (all tracked frames)
traj = [[round(r[1], 4), round(r[2], 4), round(r[3], 4)] for r in load_tum(f"{OUT}/f_mv.txt")]
kftraj = [[round(r[1], 4), round(r[2], 4), round(r[3], 4)] for r in kfs]
json.dump({"points": pts, "traj": traj, "kftraj": kftraj,
           "n_pts": len(pts), "n_kf": len(kfs)},
          open("/home/garuda/euroc_live/cloud.json", "w"))
print(f"wrote cloud.json: {len(pts)} points, {len(traj)} traj poses, {len(kfs)} KFs")
