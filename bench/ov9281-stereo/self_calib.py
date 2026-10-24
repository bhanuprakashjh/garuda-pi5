#!/usr/bin/env python3
"""Scene self-calibration of the OV9281 room1 stereo extrinsics.
Pool feature correspondences across many frames (the pair is rigid), estimate one essential
matrix, refine R,t by minimizing Sampson error, auto-fix L/R order, validate vertical alignment,
then rebuild the clip + ORB-SLAM3 yaml. Intrinsics reused from the mono calib (imperfect but ok)."""
import glob
import os

import cv2
import numpy as np

OV = "/home/garuda/euroc_live/room1/mav0"
CAL = "/home/garuda/garuda-sdk-deploy/bench/camarray_stereo.yaml"  # persistent; intrinsics only
OUT = "/home/garuda/euroc_live/room1_selfcal"
B = 0.047524


def g(fs, n): return fs.getNode(n).mat()
def gray(p): return cv2.imread(p, 0)


fs = cv2.FileStorage(CAL, cv2.FILE_STORAGE_READ)
K1, D1, K2, D2 = g(fs, "K1"), g(fs, "D1"), g(fs, "K2"), g(fs, "D2")
fs.release()

l = sorted(glob.glob(f"{OV}/cam0/data/*.png")); r = sorted(glob.glob(f"{OV}/cam1/data/*.png"))
h, w = gray(l[0]).shape
orb = cv2.ORB_create(2500, fastThreshold=12)
bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

# ---- pool inlier correspondences over the clip ----
X1, X2, rvecs, tdirs = [], [], [], []
for i in range(2, len(l), 2):
    A, Bi = gray(l[i]), gray(r[i])
    ka, da = orb.detectAndCompute(A, None); kb, db = orb.detectAndCompute(Bi, None)
    if da is None or db is None: continue
    ms = [m for m in bf.match(da, db) if m.distance < 40]
    if len(ms) < 60: continue
    p1 = np.float32([ka[m.queryIdx].pt for m in ms]).reshape(-1, 1, 2)
    p2 = np.float32([kb[m.trainIdx].pt for m in ms]).reshape(-1, 1, 2)
    n1 = cv2.undistortPoints(p1, K1, D1).reshape(-1, 2)
    n2 = cv2.undistortPoints(p2, K2, D2).reshape(-1, 2)
    E, mask = cv2.findEssentialMat(n1, n2, np.eye(3), cv2.RANSAC, 0.9999, 8e-4)
    if E is None or E.shape != (3, 3): continue
    mask = mask.ravel().astype(bool)
    ninl, R, t, mp = cv2.recoverPose(E, n1[mask], n2[mask], np.eye(3))
    rvecs.append(cv2.Rodrigues(R)[0].ravel()); tdirs.append((t.ravel() / np.linalg.norm(t)))
    X1.append(n1[mask]); X2.append(n2[mask])
X1 = np.vstack(X1); X2 = np.vstack(X2)
X1h = np.hstack([X1, np.ones((len(X1), 1))]); X2h = np.hstack([X2, np.ones((len(X2), 1))])
rvec0 = np.median(rvecs, 0); tdir0 = np.median(tdirs, 0); tdir0 /= np.linalg.norm(tdir0)
print(f"pooled {len(X1)} inlier corr over {len(rvecs)} frames")
print(f"init rvec {rvec0.round(3)} ({np.degrees(np.linalg.norm(rvec0)):.1f} deg)  tdir {tdir0.round(3)}")


def E_from(rvec, t):
    R = cv2.Rodrigues(rvec)[0]
    tx = np.array([[0, -t[2], t[1]], [t[2], 0, -t[0]], [-t[1], t[0], 0]])
    return tx @ R


def sampson(params):
    E = E_from(params[:3], params[3:6])
    Ex1 = X1h @ E.T; Etx2 = X2h @ E
    num = np.sum(X2h * Ex1, axis=1)
    den = Ex1[:, 0]**2 + Ex1[:, 1]**2 + Etx2[:, 0]**2 + Etx2[:, 1]**2
    return num / np.sqrt(den + 1e-12)


# Gauss-Newton refinement (numpy only): minimize Sampson error over pooled correspondences
p = np.hstack([rvec0, tdir0])
rms0 = np.sqrt(np.mean(sampson(p)**2))
for it in range(15):
    resid = sampson(p)                       # NB: not 'r' — 'r' is the right-image path list
    J = np.zeros((len(resid), 6))
    for k in range(6):
        dp = p.copy(); eps = 1e-6 * (1 + abs(p[k])); dp[k] += eps
        J[:, k] = (sampson(dp) - resid) / eps
    H = J.T @ J + 1e-9 * np.eye(6)
    step = np.linalg.solve(H, J.T @ resid)
    pn = p - step
    if np.sqrt(np.mean(sampson(pn)**2)) < np.sqrt(np.mean(sampson(p)**2)):
        p = pn
    else:
        break
rvec, t = p[:3], p[3:6]
print(f"refined (GN): {np.degrees(np.linalg.norm(rvec)):.2f} deg  tdir {(t/np.linalg.norm(t)).round(3)}  "
      f"sampson rms {rms0:.2e} -> {np.sqrt(np.mean(sampson(p)**2)):.2e}")
R = cv2.Rodrigues(rvec)[0]; t = t / np.linalg.norm(t)


def make_maps(KL, DL, KR, DR, Rr, Tr):
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(KL, DL, KR, DR, (w, h), Rr, Tr.reshape(3, 1),
                                                 flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    return (cv2.initUndistortRectifyMap(KL, DL, R1, P1, (w, h), cv2.CV_32FC1),
            cv2.initUndistortRectifyMap(KR, DR, R2, P2, (w, h), cv2.CV_32FC1), P1)


def val(mL, mR, swap):
    dyy, dxx = [], []
    for i in range(4, len(l), 12):
        A = cv2.remap(gray(r[i] if swap else l[i]), mL[0], mL[1], cv2.INTER_LINEAR)
        Bb = cv2.remap(gray(l[i] if swap else r[i]), mR[0], mR[1], cv2.INTER_LINEAR)
        ka, da = orb.detectAndCompute(A, None); kb, db = orb.detectAndCompute(Bb, None)
        ms = [m for m in bf.match(da, db) if m.distance < 45]
        dyy += [ka[m.queryIdx].pt[1] - kb[m.trainIdx].pt[1] for m in ms]
        dxx += [ka[m.queryIdx].pt[0] - kb[m.trainIdx].pt[0] for m in ms]
    dyy = np.abs(np.array(dyy)); dxx = np.array(dxx)
    return np.median(dyy), np.mean(dyy < 2), np.median(dxx), np.mean(dxx > 0)


# choose ordering giving positive disparity
best = None
for swap in (False, True):
    if not swap:
        mL, mR, P1 = make_maps(K1, D1, K2, D2, R, t * B)
    else:
        mL, mR, P1 = make_maps(K2, D2, K1, D1, R.T, (-R.T @ (t * B)))
    mdy, f2, mdx, fdx = val(mL, mR, swap)
    print(f"ordering swap={swap}: |dy|med={mdy:.2f} frac|dy|<2={f2:.2f} disp_med={mdx:.1f} dx>0={fdx:.2f}")
    if fdx > 0.5 and (best is None or mdy < best[0]):
        best = (mdy, swap, mL, mR, P1)

mdy, swap, mL, mR, P1 = best
print(f"\nCHOSEN swap={swap}  |dy|med={mdy:.2f}px")
fx, fy, cx, cy = P1[0, 0], P1[1, 1], P1[0, 2], P1[1, 2]

# ---- build clip ----
os.system(f"rm -rf {OUT}; mkdir -p {OUT}/mav0/cam0/data {OUT}/mav0/cam1/data")
left = r if swap else l
right = l if swap else r
for lf, rf in zip(left, right):
    name = os.path.basename(lf)
    cv2.imwrite(f"{OUT}/mav0/cam0/data/{name}", cv2.remap(gray(lf), mL[0], mL[1], cv2.INTER_LINEAR))
    cv2.imwrite(f"{OUT}/mav0/cam1/data/{name}", cv2.remap(gray(rf), mR[0], mR[1], cv2.INTER_LINEAR))
names = sorted(os.path.splitext(os.path.basename(f))[0] for f in glob.glob(f"{OUT}/mav0/cam0/data/*.png"))
open(f"{OUT}/times.txt", "w").write("\n".join(names) + "\n")
yaml = f"""%YAML:1.0
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
Camera.width: {w}
Camera.height: {h}
Camera.fps: 20
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
"""
open(f"{OUT}/cam.yaml", "w").write(yaml)
print(f"wrote {OUT} ({len(names)} frames) fx={fx:.1f} cx={cx:.1f} cy={cy:.1f}")
