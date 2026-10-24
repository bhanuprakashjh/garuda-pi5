#!/usr/bin/env python3
"""Desync test: compare per-frame stereo geometry stability + rectified |dy| on a STATIC clip vs
the MOVING room1 clip. If static is tight (low rotation spread, ~1px dy) while room1 drifts, the
~3px ceiling is motion-induced desync. If static is just as loose, it's a fixed calib/intrinsic error."""
import glob

import cv2
import numpy as np

CAL = "/home/garuda/garuda-sdk-deploy/bench/camarray_stereo.yaml"
B = 0.047524


def g(fs, n): return fs.getNode(n).mat()
def gray(p): return cv2.imread(p, 0)


fs = cv2.FileStorage(CAL, cv2.FILE_STORAGE_READ)
K1, D1, K2, D2 = g(fs, "K1"), g(fs, "D1"), g(fs, "K2"), g(fs, "D2")
fs.release()
orb = cv2.ORB_create(2500, fastThreshold=12); bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)


def pose(A, Bimg):
    ka, da = orb.detectAndCompute(A, None); kb, db = orb.detectAndCompute(Bimg, None)
    ms = [m for m in bf.match(da, db) if m.distance < 40]
    if len(ms) < 50:
        return None
    p1 = np.float32([ka[m.queryIdx].pt for m in ms]).reshape(-1, 1, 2)
    p2 = np.float32([kb[m.trainIdx].pt for m in ms]).reshape(-1, 1, 2)
    n1 = cv2.undistortPoints(p1, K1, D1); n2 = cv2.undistortPoints(p2, K2, D2)
    E, mask = cv2.findEssentialMat(n1, n2, np.eye(3), cv2.RANSAC, 0.9999, 8e-4)
    if E is None or E.shape != (3, 3):
        return None
    _, R, t, _ = cv2.recoverPose(E, n1[mask.ravel() > 0], n2[mask.ravel() > 0], np.eye(3))
    return cv2.Rodrigues(R)[0].ravel(), (t.ravel() / np.linalg.norm(t))


def dy_global(rvec, tdir, lf, rf):
    R = cv2.Rodrigues(rvec)[0]; T = tdir * B
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K2, D2, K1, D1, (1280, 800), R.T, (-R.T @ T).reshape(3, 1),
                                                 flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    mL = cv2.initUndistortRectifyMap(K2, D2, R1, P1, (1280, 800), cv2.CV_32FC1)
    mR = cv2.initUndistortRectifyMap(K1, D1, R2, P2, (1280, 800), cv2.CV_32FC1)
    A = cv2.remap(gray(rf), mL[0], mL[1], cv2.INTER_LINEAR)   # left = cam1
    Bb = cv2.remap(gray(lf), mR[0], mR[1], cv2.INTER_LINEAR)  # right = cam0
    ka, da = orb.detectAndCompute(A, None); kb, db = orb.detectAndCompute(Bb, None)
    ms = [m for m in bf.match(da, db) if m.distance < 45]
    if len(ms) < 20:
        return None
    dy = np.abs([ka[m.queryIdx].pt[1] - kb[m.trainIdx].pt[1] for m in ms])
    return np.median(dy)


def analyze(name, d, step):
    l = sorted(glob.glob(f"{d}/mav0/cam0/data/*.png")); r = sorted(glob.glob(f"{d}/mav0/cam1/data/*.png"))
    # staticness: mean abs diff of consecutive cam0 frames
    diffs = [float(np.mean(np.abs(gray(l[i]).astype(int) - gray(l[i - 1]).astype(int))))
             for i in range(1, min(len(l), 20))]
    poses = []
    for i in range(0, len(l), step):
        pr = pose(gray(l[i]), gray(r[i]))
        if pr:
            poses.append((i, pr[0], pr[1]))
    rots = np.array([np.degrees(np.linalg.norm(p[1])) for p in poses])
    rvec_g = np.median([p[1] for p in poses], 0); td_g = np.median([p[2] for p in poses], 0)
    td_g /= np.linalg.norm(td_g)
    dys = [dy_global(rvec_g, td_g, l[p[0]], r[p[0]]) for p in poses]
    dys = [x for x in dys if x is not None]
    print(f"{name:10} | scene motion {np.median(diffs):5.1f} gray/frame | "
          f"rotation {rots.mean():.2f}° ± {rots.std():.2f}° | global-pose |dy| median {np.median(dys):.2f}px "
          f"(n={len(dys)})")
    return rots.std(), np.median(dys)


print(f"{'clip':10} | {'staticness':>10} | {'per-frame geometry':>20} | rectified alignment")
s_std, s_dy = analyze("static1", "/home/garuda/euroc_live/static1", 1)
r_std, r_dy = analyze("room1", "/home/garuda/euroc_live/room1", 3)
print()
print(f"rotation spread: static {s_std:.2f}° vs moving {r_std:.2f}°   |   |dy|: static {s_dy:.2f}px vs moving {r_dy:.2f}px")
if s_dy < 0.6 * r_dy and s_std < 0.6 * r_std:
    print("=> STATIC is markedly tighter -> the ceiling is MOTION-INDUCED DESYNC (hardware sync / IMX296 trigger fixes it).")
elif s_dy > 0.8 * r_dy:
    print("=> STATIC is just as loose -> NOT desync; a fixed calibration/intrinsic error is the limiter.")
else:
    print("=> partial: some motion component, but not the whole story.")
