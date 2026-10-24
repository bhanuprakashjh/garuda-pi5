#!/usr/bin/env python3
"""Rebuild the OV9281 room1 clip with CORRECT stereo geometry recovered from the images,
auto-fixing the L/R swap, then write an ORB-SLAM3 stereo yaml. Prints a sanity funnel.
Output: /home/garuda/euroc_live/room1_fixed/{mav0,times.txt,cam.yaml}"""
import glob
import os

import cv2
import numpy as np

OV = "/home/garuda/euroc_live/room1/mav0"
CAL = "/home/garuda/garuda-sdk-deploy/bench/camarray_stereo.yaml"
OUT = "/home/garuda/euroc_live/room1_fixed"
B = 0.047524  # assumed baseline (m) from calib magnitude; sets metric scale only


def gv(fs, n): return fs.getNode(n).mat()
def gray(p): return cv2.imread(p, cv2.IMREAD_GRAYSCALE)


fs = cv2.FileStorage(CAL, cv2.FILE_STORAGE_READ)
K1, D1, K2, D2 = gv(fs, "K1"), gv(fs, "D1"), gv(fs, "K2"), gv(fs, "D2")
fs.release()

ov_l = sorted(glob.glob(f"{OV}/cam0/data/*.png"))
ov_r = sorted(glob.glob(f"{OV}/cam1/data/*.png"))
h, w = gray(ov_l[0]).shape
orb = cv2.ORB_create(nfeatures=2000, fastThreshold=12)
bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

# ---- pool correspondences across the clip for a robust essential matrix ----
N1, N2 = [], []
for i in range(5, len(ov_l), 6):
    A, Bi = gray(ov_l[i]), gray(ov_r[i])
    ka, da = orb.detectAndCompute(A, None); kb, db = orb.detectAndCompute(Bi, None)
    if da is None or db is None: continue
    for m in bf.match(da, db):
        if m.distance < 45:
            N1.append(ka[m.queryIdx].pt); N2.append(kb[m.trainIdx].pt)
p1 = np.float32(N1).reshape(-1, 1, 2); p2 = np.float32(N2).reshape(-1, 1, 2)
n1 = cv2.undistortPoints(p1, K1, D1); n2 = cv2.undistortPoints(p2, K2, D2)
E, me = cv2.findEssentialMat(n1, n2, np.eye(3), cv2.RANSAC, 0.999, 1e-3)
inl, R, t, mp = cv2.recoverPose(E, n1, n2, np.eye(3), mask=me.copy())
t = t.ravel()
print(f"pooled corr: {len(N1)}  E-inliers: {int(me.sum())}  recoverPose-inliers: {inl}")
print(f"recovered baseline dir {t/np.linalg.norm(t)}  rot {np.degrees(np.linalg.norm(cv2.Rodrigues(R)[0])):.1f} deg")


def make_maps(KL, DL, KR, DR, Rrel, Trel):
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(KL, DL, KR, DR, (w, h), Rrel, Trel.reshape(3, 1),
                                                 flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    mL = cv2.initUndistortRectifyMap(KL, DL, R1, P1, (w, h), cv2.CV_32FC1)
    mR = cv2.initUndistortRectifyMap(KR, DR, R2, P2, (w, h), cv2.CV_32FC1)
    return mL, mR, P1, P2


def eval_cfg(mL, mR):
    """median |dy| and median disparity of good matches on a sample rectified pair."""
    j = len(ov_l) // 2
    L = cv2.remap(gray(ov_l[j]) if CFG == 0 else gray(ov_r[j]), mL[0], mL[1], cv2.INTER_LINEAR)
    Rr = cv2.remap(gray(ov_r[j]) if CFG == 0 else gray(ov_l[j]), mR[0], mR[1], cv2.INTER_LINEAR)
    ka, da = orb.detectAndCompute(L, None); kb, db = orb.detectAndCompute(Rr, None)
    ms = [m for m in bf.match(da, db) if m.distance < 45]
    dy = np.array([ka[m.queryIdx].pt[1] - kb[m.trainIdx].pt[1] for m in ms])
    dx = np.array([ka[m.queryIdx].pt[0] - kb[m.trainIdx].pt[0] for m in ms])
    return np.median(np.abs(dy)), np.median(dx), L, Rr

# ---- try config 0 (cam0=left) and config 1 (cam1=left); pick positive disparity ----
best = None
for CFG in (0, 1):
    if CFG == 0:
        mL, mR, P1, P2 = make_maps(K1, D1, K2, D2, R, t * B)
    else:  # swap: left=cam1, right=cam0; invert pose
        mL, mR, P1, P2 = make_maps(K2, D2, K1, D1, R.T, (-R.T @ (t * B)))
    mdy, mdx, L, Rr = eval_cfg(mL, mR)
    print(f"cfg{CFG} (left={'cam0' if CFG==0 else 'cam1'}): |dy|med={mdy:.1f}  disp_med={mdx:.1f}")
    if mdx > 0 and (best is None or mdy < best[1]):
        best = (CFG, mdy, mL, mR, P1, P2)

if best is None:
    print("no config gave positive disparity; aborting"); raise SystemExit(1)
CFGb, mdyb, mL, mR, P1, P2 = best
print(f"\nCHOSEN cfg{CFGb}  |dy|med={mdyb:.1f}px")

# sanity: same-row funnel on chosen config
L = cv2.remap(gray(ov_l[len(ov_l)//2]) if CFGb == 0 else gray(ov_r[len(ov_l)//2]), mL[0], mL[1], cv2.INTER_LINEAR)
Rr = cv2.remap(gray(ov_r[len(ov_l)//2]) if CFGb == 0 else gray(ov_l[len(ov_l)//2]), mR[0], mR[1], cv2.INTER_LINEAR)
kL, dL = orb.detectAndCompute(L, None); kR, dR = orb.detectAndCompute(Rr, None)
rows = {}
for j, k in enumerate(kR): rows.setdefault(int(round(k.pt[1])), []).append(j)
bh = []
for idx, k in enumerate(kL):
    y, x = k.pt[1], k.pt[0]; cc = [j for ry in range(int(y-2), int(y+2)+1) for j in rows.get(ry, []) if 0 <= x-kR[j].pt[0] <= 160]
    if cc: bh.append(min(cv2.norm(dL[idx], dR[j], cv2.NORM_HAMMING) for j in cc))
bh = np.array(bh) if bh else np.array([255])
print(f"corrected same-row(±2) funnel: rowOK={len(bh)} descPass(<50)={(bh<50).sum()} medHam={int(np.median(bh))}")

# ---- write full corrected clip ----
os.system(f"rm -rf {OUT}; mkdir -p {OUT}/mav0/cam0/data {OUT}/mav0/cam1/data")
leftlist = ov_l if CFGb == 0 else ov_r
rightlist = ov_r if CFGb == 0 else ov_l
for lf, rf in zip(leftlist, rightlist):
    name = os.path.basename(lf) if CFGb == 0 else os.path.basename(rf)
    cv2.imwrite(f"{OUT}/mav0/cam0/data/{name}", cv2.remap(gray(lf), mL[0], mL[1], cv2.INTER_LINEAR))
    cv2.imwrite(f"{OUT}/mav0/cam1/data/{name}", cv2.remap(gray(rf), mR[0], mR[1], cv2.INTER_LINEAR))
os.system(f"cp {OV}/../times.txt {OUT}/times.txt 2>/dev/null || cp /home/garuda/euroc_live/room1_rect/times.txt {OUT}/times.txt")
# times.txt must match names; rebuild from actual left filenames to be safe
names = sorted(os.path.splitext(os.path.basename(f))[0] for f in glob.glob(f"{OUT}/mav0/cam0/data/*.png"))
open(f"{OUT}/times.txt", "w").write("\n".join(names) + "\n")

fx, fy, cx, cy = P1[0, 0], P1[1, 1], P1[0, 2], P1[1, 2]
yaml = f"""%YAML:1.0
File.version: "1.0"
# OV9281 room1 re-rectified from IMAGE-RECOVERED extrinsics (2026-08-03 correction). BENCH/EVAL.
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
Camera.width: {w}
Camera.height: {h}
Camera.fps: 20
Camera.RGB: 1
Stereo.ThDepth: 40.0
Stereo.T_c1_c2: !!opencv-matrix
  rows: 4
  cols: 4
  dt: f
  data: [1.0,0.0,0.0,{B:.9f},
         0.0,1.0,0.0,0.0,
         0.0,0.0,1.0,0.0,
         0.0,0.0,0.0,1.0]
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
"""
open(f"{OUT}/cam.yaml", "w").write(yaml)
print(f"\nwrote {OUT} ({len(names)} frames), rectified fx={fx:.1f} cx={cx:.1f} cy={cy:.1f}, baseline {B*1000:.1f}mm")
