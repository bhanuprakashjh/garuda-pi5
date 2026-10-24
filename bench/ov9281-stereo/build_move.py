#!/usr/bin/env python3
"""Rebuild the move1 clip rectified with the STILL-fit stereo geometry (the clean calibration),
+ ORB-SLAM3 yaml. Then ORB-SLAM3 is run separately to see if the small motion residual still tracks."""
import glob
import os

import cv2
import numpy as np

D = "/home/garuda/euroc_live/move1/mav0"
CAL = "/home/garuda/garuda-sdk-deploy/bench/camarray_stereo.yaml"
OUT = "/home/garuda/euroc_live/move1_fit"
B = 0.047524


def g(fs, n): return fs.getNode(n).mat()
def gray(p): return cv2.imread(p, 0)


fs = cv2.FileStorage(CAL, cv2.FILE_STORAGE_READ)
K1, D1, K2, D2 = g(fs, "K1"), g(fs, "D1"), g(fs, "K2"), g(fs, "D2")
fs.release()
l = sorted(glob.glob(f"{D}/cam0/data/*.png")); r = sorted(glob.glob(f"{D}/cam1/data/*.png"))
orb = cv2.ORB_create(2500, fastThreshold=12); bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
Lc = [orb.detectAndCompute(gray(p), None) for p in l]


def mm(a, b, thr):
    if a[1] is None or b[1] is None: return []
    return [m for m in bf.match(a[1], b[1]) if m.distance < thr]


# still frames (low inter-frame flow)
still = []
for i in range(1, len(l)):
    ms = mm(Lc[i - 1], Lc[i], 45)
    if len(ms) < 20: continue
    fl = np.median([np.hypot(Lc[i][0][m.trainIdx].pt[0] - Lc[i - 1][0][m.queryIdx].pt[0],
                             Lc[i][0][m.trainIdx].pt[1] - Lc[i - 1][0][m.queryIdx].pt[1]) for m in ms])
    if fl < 1.5: still.append(i)

# pool still stereo corr -> essential -> R,t
X1, X2 = [], []
for i in still:
    Ri = orb.detectAndCompute(gray(r[i]), None)
    ms = [m for m in bf.match(Lc[i][1], Ri[1]) if m.distance < 40] if Ri[1] is not None else []
    if len(ms) < 40: continue
    p1 = np.float32([Lc[i][0][m.queryIdx].pt for m in ms]).reshape(-1, 1, 2)
    p2 = np.float32([Ri[0][m.trainIdx].pt for m in ms]).reshape(-1, 1, 2)
    n1 = cv2.undistortPoints(p1, K1, D1).reshape(-1, 2); n2 = cv2.undistortPoints(p2, K2, D2).reshape(-1, 2)
    E, mask = cv2.findEssentialMat(n1, n2, np.eye(3), cv2.RANSAC, 0.9999, 8e-4)
    if E is None or E.shape != (3, 3): continue
    mask = mask.ravel().astype(bool)
    X1.append(n1[mask]); X2.append(n2[mask])
X1 = np.vstack(X1); X2 = np.vstack(X2)
E, _ = cv2.findEssentialMat(X1, X2, np.eye(3), cv2.RANSAC, 0.9999, 6e-4)
_, R, t, _ = cv2.recoverPose(E, X1, X2, np.eye(3))
t = t.ravel() / np.linalg.norm(t)
print(f"still-fit geometry {np.degrees(np.linalg.norm(cv2.Rodrigues(R)[0])):.2f} deg from {len(still)} still frames")

# swapped ordering (established): left=cam1, right=cam0
R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K2, D2, K1, D1, (1280, 800), R.T, (-R.T @ (t * B)).reshape(3, 1),
                                            flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
mL = cv2.initUndistortRectifyMap(K2, D2, R1, P1, (1280, 800), cv2.CV_32FC1)
mR = cv2.initUndistortRectifyMap(K1, D1, R2, P2, (1280, 800), cv2.CV_32FC1)
fx, fy, cx, cy = P1[0, 0], P1[1, 1], P1[0, 2], P1[1, 2]

os.system(f"rm -rf {OUT}; mkdir -p {OUT}/mav0/cam0/data {OUT}/mav0/cam1/data")
for lf, rf in zip(l, r):
    name = os.path.basename(lf)
    cv2.imwrite(f"{OUT}/mav0/cam0/data/{name}", cv2.remap(gray(rf), mL[0], mL[1], cv2.INTER_LINEAR))  # left=cam1
    cv2.imwrite(f"{OUT}/mav0/cam1/data/{name}", cv2.remap(gray(lf), mR[0], mR[1], cv2.INTER_LINEAR))  # right=cam0
names = sorted(os.path.splitext(os.path.basename(f))[0] for f in glob.glob(f"{OUT}/mav0/cam0/data/*.png"))
open(f"{OUT}/times.txt", "w").write("\n".join(names) + "\n")
open(f"{OUT}/cam.yaml", "w").write(f"""%YAML:1.0
File.version: "1.0"
Camera.type: "PinHole"
Camera1.fx: {fx:.6f}
Camera1.fy: {fy:.6f}
Camera1.cx: {cx:.6f}
Camera1.cy: {cy:.6f}
Camera1.k1: 0.0
Camera1.k2: 0.0
Camera1.p1: 0.0
Camera1.p2: 0.0
Camera1.k3: 0.0
Camera2.fx: {fx:.6f}
Camera2.fy: {fy:.6f}
Camera2.cx: {cx:.6f}
Camera2.cy: {cy:.6f}
Camera2.k1: 0.0
Camera2.k2: 0.0
Camera2.p1: 0.0
Camera2.p2: 0.0
Camera2.k3: 0.0
Camera.width: 1280
Camera.height: 800
Camera.fps: 5
Camera.RGB: 1
Stereo.ThDepth: 40.0
Stereo.T_c1_c2: !!opencv-matrix
  rows: 4
  cols: 4
  dt: f
  data: [1.0,0.0,0.0,{B:.9f}, 0.0,1.0,0.0,0.0, 0.0,0.0,1.0,0.0, 0.0,0.0,0.0,1.0]
ORBextractor.nFeatures: 1200
ORBextractor.scaleFactor: 1.2
ORBextractor.nLevels: 8
ORBextractor.iniThFAST: 20
ORBextractor.minThFAST: 7
Viewer.KeyFrameSize: 0.05
Viewer.KeyFrameLineWidth: 1.0
Viewer.GraphLineWidth: 0.9
Viewer.PointSize: 2.0
Viewer.CameraSize: 0.08
Viewer.CameraLineWidth: 3.0
Viewer.ViewpointX: 0.0
Viewer.ViewpointY: -0.7
Viewer.ViewpointZ: -1.8
Viewer.ViewpointF: 500.0
Viewer.imageViewScale: 1.0
""")
print(f"wrote {OUT} ({len(names)} frames) fx={fx:.1f} cx={cx:.1f} cy={cy:.1f}")
