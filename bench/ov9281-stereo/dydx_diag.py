#!/usr/bin/env python3
"""Where do the GOOD descriptor matches land after rectification?
If corresponding points sit at dy!=0 (off epipolar row), ORB-SLAM3's same-row stereo search
rejects them -> the failure is RECTIFICATION/CALIBRATION geometry, not sensor appearance.
Measured on RAW and on rectified pairs; EuRoC as control."""
import glob

import cv2
import numpy as np

EUROC = "/home/garuda/datasets/euroc/MH_01_easy/mav0"
OV = "/home/garuda/euroc_live/room1/mav0"
CAL = "/home/garuda/garuda-sdk-deploy/bench/camarray_stereo.yaml"


def g(fs, n): return fs.getNode(n).mat()
def gray(p): return cv2.imread(p, cv2.IMREAD_GRAYSCALE)
def rmp(img, m): return cv2.remap(img, m[0], m[1], cv2.INTER_LINEAR)


def maps(K1, D1, K2, D2, R, T, size):
    R1, R2, P1, P2, Q, roi1, roi2 = cv2.stereoRectify(K1, D1, K2, D2, size, R, T,
                                                       flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    return (cv2.initUndistortRectifyMap(K1, D1, R1, P1, size, cv2.CV_32FC1),
            cv2.initUndistortRectifyMap(K2, D2, R2, P2, size, cv2.CV_32FC1))


def good_matches(A, B, thr=40):
    orb = cv2.ORB_create(nfeatures=1500, fastThreshold=12)
    ka, da = orb.detectAndCompute(A, None); kb, db = orb.detectAndCompute(B, None)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    ms = [m for m in bf.match(da, db) if m.distance < thr]
    dy = np.array([ka[m.queryIdx].pt[1] - kb[m.trainIdx].pt[1] for m in ms])
    dx = np.array([ka[m.queryIdx].pt[0] - kb[m.trainIdx].pt[0] for m in ms])
    return dy, dx


def report(tag, dy, dx):
    if len(dy) == 0:
        print(f"{tag:32} no matches"); return
    print(f"{tag:32} n={len(dy):4d}  |dy|med={np.median(np.abs(dy)):5.1f}  "
          f"dy_std={dy.std():5.1f}  frac|dy|<2={np.mean(np.abs(dy) < 2):.2f}  "
          f"dx_med={np.median(dx):6.1f}  frac dx>0={np.mean(dx > 0):.2f}")


fs = cv2.FileStorage(CAL, cv2.FILE_STORAGE_READ)
K1, D1, K2, D2, R, T = g(fs, "K1"), g(fs, "D1"), g(fs, "K2"), g(fs, "D2"), g(fs, "R"), g(fs, "T") / 1000.0
fs.release()
mov = maps(K1, D1, K2, D2, R, T, (1280, 800))
KE1 = np.array([[458.654, 0, 367.215], [0, 457.296, 248.375], [0, 0, 1]])
DE1 = np.array([-0.28340811, 0.07395907, 0.00019359, 1.76187114e-05, 0.0])
KE2 = np.array([[457.587, 0, 379.999], [0, 456.134, 255.238], [0, 0, 1]])
DE2 = np.array([-0.28368365, 0.07451284, -0.00010473, -3.55590700e-05, 0.0])
Tc = np.array([[0.999997256477797, -0.002317135723275, -0.000343393120620, 0.110074137800478],
               [0.002312067192432, 0.999898048507103, -0.014090668452683, -0.000156612054392],
               [0.000376008102320, 0.014089835846691, 0.999900662638081, 0.000889382785432],
               [0, 0, 0, 1.0]])
me = maps(KE1, DE1, KE2, DE2, Tc[:3, :3].T, (-Tc[:3, :3].T @ Tc[:3, 3]).reshape(3, 1), (752, 480))

ov_l = sorted(glob.glob(f"{OV}/cam0/data/*.png")); ov_r = sorted(glob.glob(f"{OV}/cam1/data/*.png"))
e_l = sorted(glob.glob(f"{EUROC}/cam0/data/*.png")); e_r = sorted(glob.glob(f"{EUROC}/cam1/data/*.png"))
i_ov = len(ov_l)//2; i_e = len(e_l)//2

# EuRoC rectified control
dy, dx = good_matches(rmp(gray(e_l[i_e]), me[0]), rmp(gray(e_r[i_e]), me[1]))
report("EuRoC  RECTIFIED (control)", dy, dx)
# OV9281 raw (no rectify) and rectified
dy, dx = good_matches(gray(ov_l[i_ov]), gray(ov_r[i_ov]))
report("OV9281 RAW (unrectified)", dy, dx)
dy, dx = good_matches(rmp(gray(ov_l[i_ov]), mov[0]), rmp(gray(ov_r[i_ov]), mov[1]))
report("OV9281 RECTIFIED (our calib)", dy, dx)
print("\n control should show frac|dy|<2 high (~0.8+), dx>0. If OV9281 rectified has LOW frac|dy|<2 or")
print(" dx not>0 while matches are plentiful -> rectification/calibration geometry is the culprit (fixable).")
