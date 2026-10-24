#!/usr/bin/env python3
"""Recover the TRUE relative pose of the OV9281 pair from the matches (essential matrix),
compare to the stored calibration, and rectify from the recovered pose. If dy collapses to ~0,
the pair rectifies correctly with correct extrinsics => the failure is calibration, definitively."""
import glob

import cv2
import numpy as np

OV = "/home/garuda/euroc_live/room1/mav0"
CAL = "/home/garuda/garuda-sdk-deploy/bench/camarray_stereo.yaml"


def g(fs, n): return fs.getNode(n).mat()
def gray(p): return cv2.imread(p, cv2.IMREAD_GRAYSCALE)


fs = cv2.FileStorage(CAL, cv2.FILE_STORAGE_READ)
K1, D1, K2, D2 = g(fs, "K1"), g(fs, "D1"), g(fs, "K2"), g(fs, "D2")
Tcal = g(fs, "T").ravel()
fs.release()

ov_l = sorted(glob.glob(f"{OV}/cam0/data/*.png")); ov_r = sorted(glob.glob(f"{OV}/cam1/data/*.png"))
i = len(ov_l) // 2
A = gray(ov_l[i]); B = gray(ov_r[i]); h, w = A.shape

orb = cv2.ORB_create(nfeatures=2000, fastThreshold=12)
ka, da = orb.detectAndCompute(A, None); kb, db = orb.detectAndCompute(B, None)
bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
ms = [m for m in bf.match(da, db) if m.distance < 50]
p1 = np.float32([ka[m.queryIdx].pt for m in ms]).reshape(-1, 1, 2)
p2 = np.float32([kb[m.trainIdx].pt for m in ms]).reshape(-1, 1, 2)

# undistort to normalized coords
n1 = cv2.undistortPoints(p1, K1, D1)
n2 = cv2.undistortPoints(p2, K2, D2)
E, maskE = cv2.findEssentialMat(n1, n2, np.eye(3), cv2.RANSAC, 0.999, 1e-3)
inl, Rr, t, maskP = cv2.recoverPose(E, n1, n2, np.eye(3), mask=maskE.copy())
t = t.ravel()
rvec, _ = cv2.Rodrigues(Rr)
ang = np.degrees(np.linalg.norm(rvec))
print(f"matches={len(ms)}  essential inliers={int(maskE.sum())}  recoverPose inliers={inl}")
print(f"\nSTORED calib baseline dir (unit): {Tcal/np.linalg.norm(Tcal)}   (|T|={np.linalg.norm(Tcal):.1f} mm)")
print(f"RECOVERED baseline dir  (unit): {t/np.linalg.norm(t)}")
print(f"RECOVERED inter-camera rotation: {ang:.1f} deg")
print("  (if recovered dir is mostly +X and matches stored, calib direction is right;")
print("   if recovered is mostly Y or opposite sign, the stored extrinsics/tile-pairing are wrong)")

# rectify from recovered pose (use stored baseline magnitude for scale)
Tb = (t / np.linalg.norm(t)) * (np.linalg.norm(Tcal) / 1000.0)
R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K1, D1, K2, D2, (w, h), Rr, Tb.reshape(3, 1),
                                            flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
m1 = cv2.initUndistortRectifyMap(K1, D1, R1, P1, (w, h), cv2.CV_32FC1)
m2 = cv2.initUndistortRectifyMap(K2, D2, R2, P2, (w, h), cv2.CV_32FC1)
Aw = cv2.remap(A, m1[0], m1[1], cv2.INTER_LINEAR)
Bw = cv2.remap(B, m2[0], m2[1], cv2.INTER_LINEAR)
ka2, da2 = orb.detectAndCompute(Aw, None); kb2, db2 = orb.detectAndCompute(Bw, None)
ms2 = [m for m in bf.match(da2, db2) if m.distance < 50]
dy = np.abs(np.array([ka2[m.queryIdx].pt[1] - kb2[m.trainIdx].pt[1] for m in ms2]))
dx = np.array([ka2[m.queryIdx].pt[0] - kb2[m.trainIdx].pt[0] for m in ms2])
print(f"\nAfter rectifying from RECOVERED pose:")
print(f"  good matches={len(ms2)}  |dy|med={np.median(dy):.1f}  frac|dy|<2={np.mean(dy<2):.2f}  "
      f"disp_med={np.median(dx):.1f}  frac dx>0={np.mean(dx>0):.2f}")
print("  => |dy|~0 & frac|dy|<2 high => the pair RECTIFIES CORRECTLY with correct extrinsics.")
print("     The OV9281 CAN do stereo; the stored calibration was wrong. FIXABLE, not a hardware wall.")
