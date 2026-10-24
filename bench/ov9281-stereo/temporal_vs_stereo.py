#!/usr/bin/env python3
"""Is the descriptor failure cross-camera (the PAIR) or just bad images?
Pure descriptor matching (BFMatcher crossCheck, no geometry) on:
  TEMPORAL: cam0[i] vs cam0[i+1]  -- SAME camera, adjacent time (scene barely moved)
  STEREO  : cam0[i] vs cam1[i]    -- the two cameras, same time
If temporal Hamming is LOW but stereo Hamming is HIGH -> each camera is internally consistent
(good images); only the cross-camera pair fails -> matched-optics / sync problem, not bad sensors.
"""
import glob

import cv2
import numpy as np

EUROC = "/home/garuda/datasets/euroc/MH_01_easy/mav0"
OV = "/home/garuda/euroc_live/room1/mav0"


def gray(p): return cv2.imread(p, cv2.IMREAD_GRAYSCALE)


def match_med(A, B):
    orb = cv2.ORB_create(nfeatures=1200, fastThreshold=12)
    ka, da = orb.detectAndCompute(A, None)
    kb, db = orb.detectAndCompute(B, None)
    if da is None or db is None:
        return 0, 255
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    m = bf.match(da, db)
    if not m:
        return 0, 255
    d = np.array([x.distance for x in m])
    return len(m), int(np.median(d))


for name, cam0, cam1 in [("EuRoC", f"{EUROC}/cam0/data", f"{EUROC}/cam1/data"),
                         ("OV9281", f"{OV}/cam0/data", f"{OV}/cam1/data")]:
    l = sorted(glob.glob(f"{cam0}/*.png")); r = sorted(glob.glob(f"{cam1}/*.png"))
    idxs = [len(l)//3, len(l)//2, 2*len(l)//3]
    temp, ster = [], []
    for i in idxs:
        nt, ht = match_med(gray(l[i]), gray(l[i+1]))      # same cam, next frame
        ns, hs = match_med(gray(l[i]), gray(r[i]))        # cam0 vs cam1, same time
        temp.append((nt, ht)); ster.append((ns, hs))
    tn = int(np.mean([x[0] for x in temp])); th = int(np.median([x[1] for x in temp]))
    sn = int(np.mean([x[0] for x in ster])); sh = int(np.median([x[1] for x in ster]))
    print(f"{name:8}  temporal cam0->cam0(next): {tn:4d} matches, medHam {th:3d}   |   "
          f"stereo cam0->cam1: {sn:4d} matches, medHam {sh:3d}")
print("\n low temporal Ham + high stereo Ham => images are fine per-camera; the PAIR mismatches "
      "(optics/sync) -> a matched, synced GS pair (IMX296 done right) is the fix.")
