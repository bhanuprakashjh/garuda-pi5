#!/usr/bin/env python3
"""Controlled stereo-correspondence battery — separates funnel-CODE, our-RECTIFICATION,
and the SENSOR as causes of ORB-SLAM3's OV9281 stereo-tracking failure.

Identical funnel on 3 sources:
  (1) EuRoC  — positive control (known-good stereo), clean cv2 rectify from EuRoC calib
  (2) OV9281 — SAME raw pair, rectified by CLEAN cv2 stereoRectify(K,D,R,T)
  (3) OV9281 — SAME raw pair, rectified by OUR stored R1/R2/P1/P2 (== rectify_clip.py method)

If (1) passes with the same code -> funnel code is fine.
If (2)==(3) -> our rectification is standard (no bug).
If (2)/(3) poor while (1) good -> the OV9281 pair itself is the problem (sensor/optics/calib).
If (2) good but (3) poor -> our rectification path is the bug.
"""
import glob
import os
import sys

import cv2
import numpy as np

EUROC = "/home/garuda/datasets/euroc/MH_01_easy/mav0"
OV = "/home/garuda/euroc_live/room1/mav0"
CAL = "/home/garuda/garuda-sdk-deploy/bench/camarray_stereo.yaml"


def yget(fs, name):
    m = fs.getNode(name).mat()
    return m


def orb_funnel(L, R, maxdisp=128, rowband=2.0, nfeat=1000):
    """L,R rectified grayscale. Returns dict of funnel stats."""
    orb = cv2.ORB_create(nfeatures=nfeat, scaleFactor=1.2, nlevels=8, fastThreshold=12)
    kL, dL = orb.detectAndCompute(L, None)
    kR, dR = orb.detectAndCompute(R, None)
    if dL is None or dR is None or len(kL) < 20 or len(kR) < 20:
        return dict(nkp=len(kL) if kL else 0, rowOK=0, descPass=0, medH=255, disp_ok=0, n=0)
    # bucket R keypoints by integer row for fast same-row lookup
    rows = {}
    for j, k in enumerate(kR):
        rows.setdefault(int(round(k.pt[1])), []).append(j)
    best_H = []
    disparities = []
    rowOK = 0
    for i, k in enumerate(kL):
        y, x = k.pt[1], k.pt[0]
        cand = []
        for ry in range(int(round(y - rowband)), int(round(y + rowband)) + 1):
            for j in rows.get(ry, []):
                dxp = x - kR[j].pt[0]          # disparity = xL - xR, should be >0
                if 0.0 <= dxp <= maxdisp:
                    cand.append(j)
        if not cand:
            continue
        rowOK += 1
        ds = np.array([cv2.norm(dL[i], dR[j], cv2.NORM_HAMMING) for j in cand])
        bi = int(ds.argmin())
        best_H.append(int(ds[bi]))
        disparities.append(x - kR[cand[bi]].pt[0])
    best_H = np.array(best_H) if best_H else np.array([255])
    descPass = int((best_H < 50).sum())
    return dict(nkp=len(kL), rowOK=rowOK, descPass=descPass,
                medH=int(np.median(best_H)), n=len(best_H),
                # of the "good" (<50) matches, are disparities positive & plausible?
                good_disp_med=float(np.median([d for d, h in zip(disparities, best_H) if h < 50] or [0])))


def rectify_maps(K1, D1, K2, D2, R, T, size):
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K1, D1, K2, D2, size, R, T,
                                                 flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    m1x, m1y = cv2.initUndistortRectifyMap(K1, D1, R1, P1, size, cv2.CV_32FC1)
    m2x, m2y = cv2.initUndistortRectifyMap(K2, D2, R2, P2, size, cv2.CV_32FC1)
    return (m1x, m1y), (m2x, m2y)


def maps_from_stored(K1, D1, R1, P1, K2, D2, R2, P2, size):
    m1x, m1y = cv2.initUndistortRectifyMap(K1, D1, R1, P1, size, cv2.CV_32FC1)
    m2x, m2y = cv2.initUndistortRectifyMap(K2, D2, R2, P2, size, cv2.CV_32FC1)
    return (m1x, m1y), (m2x, m2y)


def rect(img, m):
    return cv2.remap(img, m[0], m[1], cv2.INTER_LINEAR)


def load_gray(p):
    return cv2.imread(p, cv2.IMREAD_GRAYSCALE)


def agg(runs):
    ks = ("nkp", "rowOK", "descPass", "medH", "n", "good_disp_med")
    return {k: round(float(np.mean([r[k] for r in runs])), 1) for k in ks}


# ---- load OV9281 calibration ----
fs = cv2.FileStorage(CAL, cv2.FILE_STORAGE_READ)
K1 = yget(fs, "K1"); D1 = yget(fs, "D1"); K2 = yget(fs, "K2"); D2 = yget(fs, "D2")
R = yget(fs, "R"); T = yget(fs, "T") / 1000.0  # mm -> m
R1s = yget(fs, "R1"); R2s = yget(fs, "R2"); P1s = yget(fs, "P1"); P2s = yget(fs, "P2")
fs.release()
size_ov = (1280, 800)
clean_ov = rectify_maps(K1, D1, K2, D2, R, T, size_ov)
stored_ov = maps_from_stored(K1, D1, R1s, P1s, K2, D2, R2s, P2s, size_ov)

# ---- EuRoC calib (from ORB-SLAM3 EuRoC.yaml, hardcoded) ----
KE1 = np.array([[458.654, 0, 367.215], [0, 457.296, 248.375], [0, 0, 1]])
DE1 = np.array([-0.28340811, 0.07395907, 0.00019359, 1.76187114e-05, 0.0])
KE2 = np.array([[457.587, 0, 379.999], [0, 456.134, 255.238], [0, 0, 1]])
DE2 = np.array([-0.28368365, 0.07451284, -0.00010473, -3.55590700e-05, 0.0])
# Stereo.T_c1_c2 = pose of cam2 in cam1 frame -> cv2 R,T (cam1->cam2) = Rc^T, -Rc^T t
Tc = np.array([[0.999997256477797, -0.002317135723275, -0.000343393120620, 0.110074137800478],
               [0.002312067192432, 0.999898048507103, -0.014090668452683, -0.000156612054392],
               [0.000376008102320, 0.014089835846691, 0.999900662638081, 0.000889382785432],
               [0, 0, 0, 1.0]])
Rc = Tc[:3, :3]; tc = Tc[:3, 3]
RE = Rc.T; TE = (-Rc.T @ tc).reshape(3, 1)
size_e = (752, 480)
clean_e = rectify_maps(KE1, DE1, KE2, DE2, RE, TE, size_e)

# ---- pick frames ----
ov_l = sorted(glob.glob(f"{OV}/cam0/data/*.png"))
ov_r = sorted(glob.glob(f"{OV}/cam1/data/*.png"))
e_l = sorted(glob.glob(f"{EUROC}/cam0/data/*.png"))
e_r = sorted(glob.glob(f"{EUROC}/cam1/data/*.png"))
ov_idx = [len(ov_l) // 4, len(ov_l) // 2, 3 * len(ov_l) // 4]
e_idx = [len(e_l) // 3, len(e_l) // 2, 2 * len(e_l) // 3]

res = {"1_EuRoC(control, clean rectify)": [],
       "2_OV9281(clean cv2 rectify)": [],
       "3_OV9281(our stored maps)": []}

for i in e_idx:
    L = rect(load_gray(e_l[i]), clean_e[0]); Rr = rect(load_gray(e_r[i]), clean_e[1])
    res["1_EuRoC(control, clean rectify)"].append(orb_funnel(L, Rr))
for i in ov_idx:
    rawL = load_gray(ov_l[i]); rawR = load_gray(ov_r[i])
    L = rect(rawL, clean_ov[0]); Rr = rect(rawR, clean_ov[1])
    res["2_OV9281(clean cv2 rectify)"].append(orb_funnel(L, Rr))
    L2 = rect(rawL, stored_ov[0]); R2r = rect(rawR, stored_ov[1])
    res["3_OV9281(our stored maps)"].append(orb_funnel(L2, R2r))

print(f"{'source':38} {'nkp':>5} {'rowOK':>6} {'descPass<50':>12} {'medHam':>7} {'gdDisp':>7}")
for k, runs in res.items():
    a = agg(runs)
    print(f"{k:38} {a['nkp']:5.0f} {a['rowOK']:6.0f} {a['descPass']:12.0f} {a['medH']:7.0f} {a['good_disp_med']:7.1f}")
print("\nInterpretation:")
print(" (1) high descPass/low medHam => funnel code works, this is what GOOD stereo looks like")
print(" (2)==(3) => our rectify is standard (no code bug); (2)>>(3) => our rectify is the bug")
print(" (2)/(3) poor while (1) good => OV9281 pair itself is the problem (sensor/optics/calib)")
