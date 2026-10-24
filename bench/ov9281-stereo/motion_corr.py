#!/usr/bin/env python3
"""Falsifiable desync test: does the stereo vertical error scale with camera motion speed?
Desync predicts stereo_dy = dy_static + (vertical image velocity) * dt_sync  -> a straight line
whose intercept ~ the static floor and whose slope = the L/R time offset (as a fraction of a frame).
If dy is high even on near-still frames (flat / no slope), it's NOT desync."""
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
orb = cv2.ORB_create(2500, fastThreshold=12); bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)


def match(A, Bimg):
    ka, da = orb.detectAndCompute(A, None); kb, db = orb.detectAndCompute(Bimg, None)
    ms = [m for m in bf.match(da, db) if m.distance < 45]
    return ka, kb, ms


# global stereo pose (median per-frame), swapped ordering (cam1=left)
rvs, tds = [], []
for i in range(2, len(l), 3):
    ka, kb, ms = match(gray(l[i]), gray(r[i]))
    if len(ms) < 60: continue
    p1 = np.float32([ka[m.queryIdx].pt for m in ms]).reshape(-1, 1, 2)
    p2 = np.float32([kb[m.trainIdx].pt for m in ms]).reshape(-1, 1, 2)
    n1 = cv2.undistortPoints(p1, K1, D1); n2 = cv2.undistortPoints(p2, K2, D2)
    E, mask = cv2.findEssentialMat(n1, n2, np.eye(3), cv2.RANSAC, 0.9999, 8e-4)
    if E is None or E.shape != (3, 3): continue
    _, R, t, _ = cv2.recoverPose(E, n1[mask.ravel() > 0], n2[mask.ravel() > 0], np.eye(3))
    rvs.append(cv2.Rodrigues(R)[0].ravel()); tds.append(t.ravel() / np.linalg.norm(t))
rv = np.median(rvs, 0); td = np.median(tds, 0); td /= np.linalg.norm(td)
R = cv2.Rodrigues(rv)[0]; T = td * B
R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K2, D2, K1, D1, (1280, 800), R.T, (-R.T @ T).reshape(3, 1),
                                            flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
mL = cv2.initUndistortRectifyMap(K2, D2, R1, P1, (1280, 800), cv2.CV_32FC1)
mR = cv2.initUndistortRectifyMap(K1, D1, R2, P2, (1280, 800), cv2.CV_32FC1)

rows = []
for i in range(1, len(l)):
    # camera motion this frame: median |vertical| ORB displacement cam0[i-1]->cam0[i]
    ka, kb, ms = match(gray(l[i - 1]), gray(l[i]))
    if len(ms) < 40: continue
    vy = np.median([abs(ka[m.queryIdx].pt[1] - kb[m.trainIdx].pt[1]) for m in ms])
    vmag = np.median([np.hypot(ka[m.queryIdx].pt[0] - kb[m.trainIdx].pt[0],
                               ka[m.queryIdx].pt[1] - kb[m.trainIdx].pt[1]) for m in ms])
    # stereo vertical error this frame with the GLOBAL pose
    A = cv2.remap(gray(r[i]), mL[0], mL[1], cv2.INTER_LINEAR)
    Bb = cv2.remap(gray(l[i]), mR[0], mR[1], cv2.INTER_LINEAR)
    ka2, kb2, ms2 = match(A, Bb)
    if len(ms2) < 20: continue
    sdy = np.median([abs(ka2[m.queryIdx].pt[1] - kb2[m.trainIdx].pt[1]) for m in ms2])
    rows.append((vmag, vy, sdy))

rows = np.array(rows)
vmag, vy, sdy = rows[:, 0], rows[:, 1], rows[:, 2]
# fit stereo_dy = a + b * vertical_motion
b, a = np.polyfit(vy, sdy, 1)
rcorr = np.corrcoef(vy, sdy)[0, 1]
order = np.argsort(vmag)
lo = sdy[order[:len(order) // 3]]; hi = sdy[order[-len(order) // 3:]]
print(f"frames used: {len(rows)}")
print(f"slowest third: motion {np.median(vmag[order[:len(order)//3]]):.1f}px  -> stereo |dy| {lo.mean():.2f}px")
print(f"fastest third: motion {np.median(vmag[order[-len(order)//3:]]):.1f}px  -> stereo |dy| {hi.mean():.2f}px")
print(f"fit  stereo_dy = {a:.2f} + {b:.3f} * vertical_motion   (corr r={rcorr:.2f})")
print(f"  intercept {a:.2f}px ~ static floor(0.24)?  slope {b:.3f} = dt_sync as fraction of a frame "
      f"(~{b*50:.1f} ms at 20 fps)")
print("\n strong positive r + intercept near static + slope>0 => DESYNC confirmed quantitatively (dt≈slope).")
print(" flat (r~0) or high dy on slow frames => NOT desync; motion-blur / calib is the story.")
