#!/usr/bin/env python3
"""Attribute the ~3px residual: per-frame OWN pose vs one GLOBAL pose.
If own-pose dy << global-pose dy, the ceiling is per-frame geometry VARIATION (mild desync/
ego-motion between the two captures), not intrinsics -> hardware sync (IMX296 trigger) is the fix.
If own ~ global ~ 3px, it's the intrinsics -> a better checkerboard calib is the fix."""
import glob

import cv2
import numpy as np

OV = "/home/garuda/euroc_live/room1/mav0"
CAL = "/home/garuda/garuda-sdk-deploy/bench/camarray_stereo.yaml"
B = 0.047524


def g(fs, n): return fs.getNode(n).mat()
def gray(p): return cv2.imread(p, 0)


fs = cv2.FileStorage(CAL, cv2.FILE_STORAGE_READ)
K1, D1, K2, D2 = g(fs, "K1"), g(fs, "D1"), g(fs, "K2"), g(fs, "D2")
fs.release()
l = sorted(glob.glob(f"{OV}/cam0/data/*.png")); r = sorted(glob.glob(f"{OV}/cam1/data/*.png"))
h, w = gray(l[0]).shape
orb = cv2.ORB_create(2500, fastThreshold=12); bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)


def pose_from(A, B):
    ka, da = orb.detectAndCompute(A, None); kb, db = orb.detectAndCompute(B, None)
    ms = [m for m in bf.match(da, db) if m.distance < 40]
    if len(ms) < 60: return None
    p1 = np.float32([ka[m.queryIdx].pt for m in ms]).reshape(-1, 1, 2)
    p2 = np.float32([kb[m.trainIdx].pt for m in ms]).reshape(-1, 1, 2)
    n1 = cv2.undistortPoints(p1, K1, D1); n2 = cv2.undistortPoints(p2, K2, D2)
    E, mask = cv2.findEssentialMat(n1, n2, np.eye(3), cv2.RANSAC, 0.9999, 8e-4)
    if E is None or E.shape != (3, 3): return None
    _, R, t, _ = cv2.recoverPose(E, n1[mask.ravel() > 0], n2[mask.ravel() > 0], np.eye(3))
    return cv2.Rodrigues(R)[0].ravel(), t.ravel() / np.linalg.norm(t)


# global pose = median over the clip (as self_calib does)
rvs, tds = [], []
for i in range(2, len(l), 3):
    pr = pose_from(gray(l[i]), gray(r[i]))
    if pr: rvs.append(pr[0]); tds.append(pr[1])
rv_g = np.median(rvs, 0); td_g = np.median(tds, 0); td_g /= np.linalg.norm(td_g)


def dy_with(pose, i):
    R = cv2.Rodrigues(pose[0])[0]; T = pose[1] * B
    # swap ordering (cam1=left) as established
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K2, D2, K1, D1, (w, h), R.T, (-R.T @ T).reshape(3, 1),
                                                 flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    mL = cv2.initUndistortRectifyMap(K2, D2, R1, P1, (w, h), cv2.CV_32FC1)
    mR = cv2.initUndistortRectifyMap(K1, D1, R2, P2, (w, h), cv2.CV_32FC1)
    A = cv2.remap(gray(r[i]), mL[0], mL[1], cv2.INTER_LINEAR)
    Bb = cv2.remap(gray(l[i]), mR[0], mR[1], cv2.INTER_LINEAR)
    ka, da = orb.detectAndCompute(A, None); kb, db = orb.detectAndCompute(Bb, None)
    ms = [m for m in bf.match(da, db) if m.distance < 45]
    dy = np.abs([ka[m.queryIdx].pt[1] - kb[m.trainIdx].pt[1] for m in ms])
    return np.median(dy), np.mean(np.array(dy) < 2)


print(f"global pose: {np.degrees(np.linalg.norm(rv_g)):.2f} deg")
print(f"{'frame':>6} {'own|dy|':>9} {'own<2':>6} | {'global|dy|':>11} {'glob<2':>7}")
own_all, glob_all = [], []
for i in range(6, len(l), 10):
    pr = pose_from(gray(l[i]), gray(r[i]))
    if not pr: continue
    o = dy_with(pr, i); gg = dy_with((rv_g, td_g), i)
    own_all.append(o[0]); glob_all.append(gg[0])
    print(f"{i:6d} {o[0]:9.2f} {o[1]:6.2f} | {gg[0]:11.2f} {gg[1]:7.2f}")
print(f"\nMEAN own-pose |dy| = {np.mean(own_all):.2f}px   global-pose |dy| = {np.mean(glob_all):.2f}px")
print(" own << global  => per-frame geometry drifts (mild desync) -> hardware sync fixes it")
print(" own ~= global  => fixed rectification error -> intrinsics/calibration is the limiter")
