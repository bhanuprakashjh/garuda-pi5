#!/usr/bin/env python3
"""Is the OV9281 pair a VALID stereo pair with bad calibration, or genuinely broken?
Fit the fundamental matrix from the descriptor matches (RANSAC). High inlier fraction => a
single rigid epipolar geometry explains the pair => it IS valid stereo, our calib is just wrong.
Then rectify UNCALIBRATED (from F alone) and re-measure dy + same-row descPass; if dy collapses
to ~0 and descPass jumps, the pair can track with a corrected calibration."""
import glob

import cv2
import numpy as np

OV = "/home/garuda/euroc_live/room1/mav0"


def gray(p): return cv2.imread(p, cv2.IMREAD_GRAYSCALE)


def kp_desc(img):
    orb = cv2.ORB_create(nfeatures=1500, fastThreshold=12)
    return orb.detectAndCompute(img, None)


def matches(A, B, thr=50):
    ka, da = kp_desc(A); kb, db = kp_desc(B)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    ms = [m for m in bf.match(da, db) if m.distance < thr]
    p1 = np.float32([ka[m.queryIdx].pt for m in ms])
    p2 = np.float32([kb[m.trainIdx].pt for m in ms])
    return ka, kb, ms, p1, p2


def dy_stats(p1, p2, tag):
    dy = np.abs(p1[:, 1] - p2[:, 1])
    print(f"  {tag:34} n={len(p1):4d} |dy|med={np.median(dy):5.1f} frac|dy|<2={np.mean(dy < 2):.2f}")


ov_l = sorted(glob.glob(f"{OV}/cam0/data/*.png")); ov_r = sorted(glob.glob(f"{OV}/cam1/data/*.png"))
i = len(ov_l) // 2
A = gray(ov_l[i]); B = gray(ov_r[i])
h, w = A.shape

ka, kb, ms, p1, p2 = matches(A, B)
print(f"OV9281 raw cam0<->cam1: {len(ms)} good descriptor matches (Ham<50)")
dy_stats(p1, p2, "raw offsets")

F, mask = cv2.findFundamentalMat(p1, p2, cv2.FM_RANSAC, 1.5, 0.999)
mask = mask.ravel().astype(bool)
print(f"\nFundamental matrix RANSAC: {mask.sum()}/{len(mask)} inliers "
      f"({100*mask.sum()/len(mask):.0f}%)  <- high => ONE rigid epipolar geometry fits the pair")

p1i, p2i = p1[mask], p2[mask]
ok, H1, H2 = cv2.stereoRectifyUncalibrated(p1i.reshape(-1, 1, 2), p2i.reshape(-1, 1, 2), F, (w, h))
print(f"stereoRectifyUncalibrated ok={ok}")
Aw = cv2.warpPerspective(A, H1, (w, h))
Bw = cv2.warpPerspective(B, H2, (w, h))

# re-detect on rectified-by-F images and measure dy of good matches
ka2, kb2, ms2, q1, q2 = matches(Aw, Bw)
print(f"\nAfter F-based (uncalibrated) rectification:")
dy_stats(q1, q2, "F-rectified offsets")
# same-row descPass funnel on F-rectified
orb = cv2.ORB_create(nfeatures=1000, fastThreshold=12)
kL, dL = orb.detectAndCompute(Aw, None); kR, dR = orb.detectAndCompute(Bw, None)
rows = {}
for j, k in enumerate(kR): rows.setdefault(int(round(k.pt[1])), []).append(j)
bh = []
for idx, k in enumerate(kL):
    y, x = k.pt[1], k.pt[0]; cc = []
    for ry in range(int(y - 2), int(y + 2) + 1):
        for j in rows.get(ry, []):
            if 0 <= x - kR[j].pt[0] <= 160: cc.append(j)
    if cc: bh.append(min(cv2.norm(dL[idx], dR[j], cv2.NORM_HAMMING) for j in cc))
bh = np.array(bh) if bh else np.array([255])
print(f"  same-row(±2) funnel: rowOK={len(bh)} descPass(<50)={(bh<50).sum()} medHam={int(np.median(bh))}")
print("\n=> if inliers high AND F-rectified |dy|->~0 AND descPass jumps, the OV9281 pair is VALID stereo")
print("   with a WRONG calibration -> recalibrate/fix tile-pairing and it can track (NOT a hardware wall).")
