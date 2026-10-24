#!/usr/bin/env python3
"""Reproject the reconstructed 3D cloud back onto real camera frames (AR-style overlay).
world point -> chosen keyframe's camera -> pixel; draw over the actual rectified image,
coloured by depth. Emits raw + overlay JPEGs (base64) for a few keyframes."""
import glob
import json
import os

import cv2
import numpy as np

D = "/home/garuda/euroc_live/move1_fit"
OUT = "/home/garuda/euroc_live/mvout"
cloud = json.load(open("/home/garuda/euroc_live/cloud.json"))
Pw = np.array([[p[0], p[1], p[2]] for p in cloud["points"]], dtype=np.float64)

fs = cv2.FileStorage(f"{D}/cam.yaml", cv2.FILE_STORAGE_READ)
fx = fs.getNode("Camera1.fx").real(); fy = fs.getNode("Camera1.fy").real()
cx = fs.getNode("Camera1.cx").real(); cy = fs.getNode("Camera1.cy").real()
fs.release()


def q2R(qx, qy, qz, qw):
    n = np.sqrt(qx*qx+qy*qy+qz*qz+qw*qw); qx, qy, qz, qw = qx/n, qy/n, qz/n, qw/n
    return np.array([
        [1-2*(qy*qy+qz*qz), 2*(qx*qy-qz*qw), 2*(qx*qz+qy*qw)],
        [2*(qx*qy+qz*qw), 1-2*(qx*qx+qz*qz), 2*(qy*qz-qx*qw)],
        [2*(qx*qz-qy*qw), 2*(qy*qz+qx*qw), 1-2*(qx*qx+qy*qy)]])


def turbo(t):
    st = np.array([[58, 80, 182], [53, 197, 201], [122, 214, 66], [230, 192, 32], [229, 83, 60]])
    t = np.clip(t, 0, 1)*4; i = np.floor(t).astype(int); f = (t-i)[:, None]
    a = st[i]; b = st[np.minimum(4, i+1)]
    return (a+(b-a)*f)  # Nx3 RGB


kfs = [[float(x) for x in L.split()[:8]] for L in open(f"{OUT}/kf_mv.txt") if len(L.split()) >= 8]
left = sorted(glob.glob(f"{D}/mav0/cam0/data/*.png"))
lmap = {int(os.path.splitext(os.path.basename(f))[0]): f for f in left}
ns = np.array(sorted(lmap))
H, W = 800, 1280

# score keyframes by how many cloud points land in-frame; keep the best 3
scored = []
for k, (ts, tx, ty, tz, qx, qy, qz, qw) in enumerate(kfs):
    Rcw = q2R(qx, qy, qz, qw).T; tvec = np.array([tx, ty, tz])
    Xc = (Rcw @ (Pw - tvec).T).T
    Z = Xc[:, 2]
    u = fx*Xc[:, 0]/Z + cx; v = fy*Xc[:, 1]/Z + cy
    inb = (Z > 0.15) & (Z < 6) & (u >= 0) & (u < W) & (v >= 0) & (v < H)
    scored.append((int(inb.sum()), k))
scored.sort(reverse=True)
picks = [k for _, k in scored[:3]]
print("picked KFs:", picks, "in-frame counts:", [s for s, k in scored[:3]])

res = []
for k in picks:
    ts, tx, ty, tz, qx, qy, qz, qw = kfs[k]
    Rcw = q2R(qx, qy, qz, qw).T; tvec = np.array([tx, ty, tz])
    Xc = (Rcw @ (Pw - tvec).T).T
    Z = Xc[:, 2]
    u = fx*Xc[:, 0]/Z + cx; v = fy*Xc[:, 1]/Z + cy
    inb = (Z > 0.15) & (Z < 6) & (u >= 0) & (u < W) & (v >= 0) & (v < H)
    uu, vv, zz = u[inb], v[inb], Z[inb]
    order = np.argsort(-zz)  # far first so near overlay
    uu, vv, zz = uu[order], vv[order], zz[order]
    plo, phi = np.percentile(zz, 3), np.percentile(zz, 97)  # robust range, ignore outliers
    print(f"  KF{k}: depth p3={plo:.2f} median={np.median(zz):.2f} p97={phi:.2f} m")
    col = turbo(np.clip((zz - plo)/(phi - plo + 1e-9), 0, 1))  # RGB, spread across the bulk
    j = int(np.argmin(np.abs(ns - ts*1e9)))
    gray = cv2.imread(lmap[ns[j]], cv2.IMREAD_GRAYSCALE)
    raw = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    ov = (raw*0.45).astype(np.uint8)  # dim the photo so points pop
    for x, y, c in zip(uu.astype(int), vv.astype(int), col):
        cv2.circle(ov, (x, y), 2, (int(c[2]), int(c[1]), int(c[0])), -1)  # BGR

    def enc(img):
        img = cv2.resize(img, (820, int(820*H/W)), interpolation=cv2.INTER_AREA)
        ok, b = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
        return "data:image/jpeg;base64," + __import__("base64").b64encode(b).decode()
    res.append({"kf": k, "n": int(inb.sum()), "zmin": round(float(zz.min()), 2),
                "zmax": round(float(zz.max()), 2), "zlo": round(float(plo), 2), "zhi": round(float(phi), 2),
                "raw": enc(raw), "overlay": enc(ov)})

json.dump(res, open("/home/garuda/euroc_live/reproj.json", "w"))
print("wrote reproj.json:", [(r["kf"], r["n"]) for r in res])
