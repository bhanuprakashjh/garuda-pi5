#!/usr/bin/env python3
"""Descriptor-free photometric check: NCC block-match L->R along the epipolar strip.
Fixed strip geometry (template must fit inside the search strip)."""
import glob

import cv2
import numpy as np

EUROC = "/home/garuda/datasets/euroc/MH_01_easy/mav0"
OV = "/home/garuda/euroc_live/room1/mav0"
CAL = "/home/garuda/garuda-sdk-deploy/bench/camarray_stereo.yaml"


def g(fs, n): return fs.getNode(n).mat()
def rm(img, m): return cv2.remap(img, m[0], m[1], cv2.INTER_LINEAR)
def gray(p): return cv2.imread(p, cv2.IMREAD_GRAYSCALE)


def maps(K1, D1, K2, D2, R, T, size):
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K1, D1, K2, D2, size, R, T,
                                                 flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    return (cv2.initUndistortRectifyMap(K1, D1, R1, P1, size, cv2.CV_32FC1),
            cv2.initUndistortRectifyMap(K2, D2, R2, P2, size, cv2.CV_32FC1))


def ncc(L, R, half=5, band=3, maxdisp=128, npatch=300):
    h, w = L.shape
    gx = cv2.Sobel(L, cv2.CV_32F, 1, 0, 3); gy = cv2.Sobel(L, cv2.CV_32F, 0, 1, 3)
    mag = cv2.magnitude(gx, gy)
    ys = range(half + band + 1, h - half - band - 1, 5)
    xs = range(half + maxdisp + 1, w - half - 1, 5)
    cand = [(y, x) for y in ys for x in xs if mag[y, x] > 40]
    cand = cand[::max(1, len(cand) // npatch)][:npatch]
    out = []
    for (y, x) in cand:
        patch = L[y - half:y + half + 1, x - half:x + half + 1].astype(np.float32)
        strip = R[y - half - band:y + half + band + 1, x - maxdisp - half:x + half + 1].astype(np.float32)
        if strip.shape[0] < patch.shape[0] or strip.shape[1] < patch.shape[1]:
            continue
        res = cv2.matchTemplate(strip, patch, cv2.TM_CCOEFF_NORMED)
        out.append(float(res.max()))
    a = np.array(out) if out else np.array([0.0])
    return len(a), round(float(np.median(a)), 3), round(float((a > 0.8).mean()), 3), round(float((a > 0.6).mean()), 3)


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
print(f"{'source':16} {'nP':>5} {'medNCC':>7} {'frac>0.8':>9} {'frac>0.6':>9}")
for name, m, ls, rs, idxs in [
        ("EuRoC control", me, e_l, e_r, [len(e_l)//3, len(e_l)//2]),
        ("OV9281", mov, ov_l, ov_r, [len(ov_l)//3, len(ov_l)//2, 2*len(ov_l)//3])]:
    rr = [ncc(rm(gray(ls[i]), m[0]), rm(gray(rs[i]), m[1])) for i in idxs]
    nP = int(np.mean([x[0] for x in rr])); mn = np.median([x[1] for x in rr])
    f8 = np.median([x[2] for x in rr]); f6 = np.median([x[3] for x in rr])
    print(f"{name:16} {nP:5d} {mn:7.3f} {f8:9.3f} {f6:9.3f}")
print("\n medNCC ~0.9 & most >0.8 => photometric correspondence exists (matched/synced sensors would too)")
print(" medNCC low & few >0.6 => the two views don't look alike at corresponding points")
