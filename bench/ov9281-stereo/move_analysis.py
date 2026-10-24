#!/usr/bin/env python3
"""Controlled desync test on one still-then-move clip (same rig, same scene).
Classify each frame by measured inter-frame motion, fit the stereo geometry from the STILL frames
(desync-clean), then measure rectification |dy| across motion bins.
Desync => still dy low AND dy rises with motion speed. Flat => not desync (calibration/other)."""
import glob

import cv2
import numpy as np

D = "/home/garuda/euroc_live/move1/mav0"
CAL = "/home/garuda/garuda-sdk-deploy/bench/camarray_stereo.yaml"
B = 0.047524


def g(fs, n): return fs.getNode(n).mat()
def gray(p): return cv2.imread(p, 0)


fs = cv2.FileStorage(CAL, cv2.FILE_STORAGE_READ)
K1, D1, K2, D2 = g(fs, "K1"), g(fs, "D1"), g(fs, "K2"), g(fs, "D2")
fs.release()
l = sorted(glob.glob(f"{D}/cam0/data/*.png")); r = sorted(glob.glob(f"{D}/cam1/data/*.png"))
orb = cv2.ORB_create(2500, fastThreshold=12); bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
Lc = [orb.detectAndCompute(gray(p), None) for p in l]
Rc = [orb.detectAndCompute(gray(p), None) for p in r]


def mm(a, b, thr):
    if a[1] is None or b[1] is None: return []
    return [m for m in bf.match(a[1], b[1]) if m.distance < thr]


# inter-frame motion in cam0
flow = [None] * len(l)
for i in range(1, len(l)):
    ms = mm(Lc[i - 1], Lc[i], 45)
    if len(ms) < 20: continue
    d = np.array([(Lc[i][0][m.trainIdx].pt[0] - Lc[i - 1][0][m.queryIdx].pt[0],
                   Lc[i][0][m.trainIdx].pt[1] - Lc[i - 1][0][m.queryIdx].pt[1]) for m in ms])
    flow[i] = (float(np.median(np.hypot(d[:, 0], d[:, 1]))), float(np.median(d[:, 1])))
still = [i for i in range(1, len(l)) if flow[i] and flow[i][0] < 1.5]
print(f"frames {len(l)}  still(<1.5px flow) {len(still)}")

# pool still stereo correspondences -> essential -> R,t
X1, X2 = [], []
for i in still:
    ms = mm(Lc[i], Rc[i], 40)
    if len(ms) < 40: continue
    p1 = np.float32([Lc[i][0][m.queryIdx].pt for m in ms]).reshape(-1, 1, 2)
    p2 = np.float32([Rc[i][0][m.trainIdx].pt for m in ms]).reshape(-1, 1, 2)
    n1 = cv2.undistortPoints(p1, K1, D1).reshape(-1, 2); n2 = cv2.undistortPoints(p2, K2, D2).reshape(-1, 2)
    E, mask = cv2.findEssentialMat(n1, n2, np.eye(3), cv2.RANSAC, 0.9999, 8e-4)
    if E is None or E.shape != (3, 3): continue
    mask = mask.ravel().astype(bool)
    X1.append(n1[mask]); X2.append(n2[mask])
X1 = np.vstack(X1); X2 = np.vstack(X2)
E, _ = cv2.findEssentialMat(X1, X2, np.eye(3), cv2.RANSAC, 0.9999, 6e-4)
_, R, t, _ = cv2.recoverPose(E, X1, X2, np.eye(3))
t = t.ravel() / np.linalg.norm(t)
print(f"still-fit geometry: {np.degrees(np.linalg.norm(cv2.Rodrigues(R)[0])):.2f} deg, {X1.shape[0]} corr")


def maps_for(swap):
    if not swap:
        R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K1, D1, K2, D2, (1280, 800), R, (t * B).reshape(3, 1),
                                                     flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
        return (cv2.initUndistortRectifyMap(K1, D1, R1, P1, (1280, 800), cv2.CV_32FC1),
                cv2.initUndistortRectifyMap(K2, D2, R2, P2, (1280, 800), cv2.CV_32FC1))
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K2, D2, K1, D1, (1280, 800), R.T, (-R.T @ (t * B)).reshape(3, 1),
                                                 flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    return (cv2.initUndistortRectifyMap(K2, D2, R1, P1, (1280, 800), cv2.CV_32FC1),
            cv2.initUndistortRectifyMap(K1, D1, R2, P2, (1280, 800), cv2.CV_32FC1))


def sdy(i, mL, mR, swap):
    A = cv2.remap(gray(r[i] if swap else l[i]), mL[0], mL[1], cv2.INTER_LINEAR)
    Bb = cv2.remap(gray(l[i] if swap else r[i]), mR[0], mR[1], cv2.INTER_LINEAR)
    ka, da = orb.detectAndCompute(A, None); kb, db = orb.detectAndCompute(Bb, None)
    ms = [m for m in bf.match(da, db) if m.distance < 45]
    if len(ms) < 15: return None, None
    dy = np.array([ka[m.queryIdx].pt[1] - kb[m.trainIdx].pt[1] for m in ms])
    dx = np.array([ka[m.queryIdx].pt[0] - kb[m.trainIdx].pt[0] for m in ms])
    return np.median(np.abs(dy)), np.median(dx)


# pick swap giving positive disparity on still frames
sw = False
for swap in (False, True):
    mL, mR = maps_for(swap)
    ds = [sdy(i, mL, mR, swap)[1] for i in still[:5]]
    ds = [x for x in ds if x is not None]
    if np.median(ds) > 0: sw = swap; break
mL, mR = maps_for(sw)
print(f"L/R swap={sw}")

# per-frame stereo dy + motion, binned
rows = []
for i in range(1, len(l)):
    if flow[i] is None: continue
    mdy, _ = sdy(i, mL, mR, sw)
    if mdy is None: continue
    rows.append((flow[i][0], mdy))
rows = np.array(rows)
fl, dy = rows[:, 0], rows[:, 1]
bins = [("still  <1.5px", fl < 1.5), ("slow 1.5-6", (fl >= 1.5) & (fl < 6)),
        ("med  6-15", (fl >= 6) & (fl < 15)), ("fast >15", fl >= 15)]
print(f"\n{'motion bin':14} {'n':>4} {'median motion':>14} {'stereo |dy| median':>20}")
for name, sel in bins:
    if sel.sum() == 0: continue
    print(f"{name:14} {int(sel.sum()):4d} {np.median(fl[sel]):14.1f} {np.median(dy[sel]):20.2f}")
b, a = np.polyfit(fl, dy, 1); rr = np.corrcoef(fl, dy)[0, 1]
print(f"\nfit stereo_dy = {a:.2f} + {b:.4f}*motion   corr r={rr:.2f}")
print(" desync => still dy low AND dy climbs with motion (positive slope, r>0.5).")
print(" flat/low across all bins => NOT desync; the earlier room1 3px was a room1-specific artifact.")
