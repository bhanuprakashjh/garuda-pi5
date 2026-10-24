#!/usr/bin/env python3
"""Grab a fresh CamArray stereo pair and report the stereo-match funnel — the SLAM-relevant metric.
Compares against the pre-focus baseline (descPass≈40 of 1200, median best-Hamming≈100). If focusing
the lenses helped, descPass rises and median-Hamming falls. Also prints per-camera sharpness."""
from __future__ import annotations
import sys, time
from collections import defaultdict
from pathlib import Path
import cv2, numpy as np

W, H, COLS = 5120, 800, 4
TILE_W = W // COLS


def main():
    repo = Path(__file__).resolve().parents[2]
    for p in ("interfaces", "hosts/pi5", "garuda-camera", "services/garuda-camera"):
        sys.path.insert(0, str(repo / p))
    from camarray_backend import CamArrayBackend
    from stereo_rig import StereoRig

    g = {"L": None, "R": None, "n": 0}

    def on_frame(meta, mv):
        if g["n"] >= 10:
            return
        arr = np.frombuffer(mv, np.uint8).reshape(H, W)
        g["L"] = np.ascontiguousarray(arr[:, 0:TILE_W])
        g["R"] = np.ascontiguousarray(arr[:, TILE_W:2 * TILE_W])
        g["n"] += 1

    b = CamArrayBackend()
    b.configure(W, H, 30, pixel_format="GREY")
    b.start(on_frame)
    t0 = time.monotonic()
    while g["n"] < 10 and time.monotonic() - t0 < 12:
        time.sleep(0.2)
    b.stop()
    if g["L"] is None:
        print("ERROR: no frames captured"); return

    def sharp(img):
        c = img[250:550, 490:790]
        return cv2.Laplacian(c, cv2.CV_64F).var()

    print(f"SHARPNESS  L={sharp(g['L']):.0f}  R={sharp(g['R']):.0f}  (want both high AND close)")

    rig = StereoRig(repo / "bench" / "camarray_stereo.yaml")
    rc1, rc2 = rig.rectify(g["L"], g["R"])
    L, R = rig.order_for_matching(rc1, rc2)

    def funnel(Li, Ri, tag):
        orb = cv2.ORB_create(1200)
        kL, dL = orb.detectAndCompute(Li, None)
        kR, dR = orb.detectAndCompute(Ri, None)
        rows = defaultdict(list)
        for j, k in enumerate(kR):
            rows[int(round(k.pt[1]))].append(j)
        bests = []
        for i, k in enumerate(kL):
            y, x = k.pt[1], k.pt[0]
            best = 256
            for dy in range(-3, 4):
                for j in rows.get(int(round(y)) + dy, []):
                    if 0 < x - kR[j].pt[0] < 250:
                        d = cv2.norm(dL[i], dR[j], cv2.NORM_HAMMING)
                        if d < best:
                            best = d
            if best < 256:
                bests.append(best)
        b = np.array(bests) if bests else np.array([256])
        print(f"{tag:14s} descPass(<75)={int((b < 75).sum()):3d}  median_Hamming={np.median(b):.0f}")

    funnel(L, R, "RAW")
    funnel(cv2.GaussianBlur(L, (3, 3), 0), cv2.GaussianBlur(R, (3, 3), 0), "blur3 (denoise)")
    funnel(cv2.GaussianBlur(L, (5, 5), 0), cv2.GaussianBlur(R, (5, 5), 0), "blur5 (denoise)")
    fm = cv2.fastNlMeansDenoising
    funnel(fm(L, None, 7, 7, 21), fm(R, None, 7, 7, 21), "NLmeans")
    print("BASELINE: descPass≈40, Hamming≈100  |  GOOD: descPass 200+, Hamming <64  (if denoise jumps it -> noise-limited)")


if __name__ == "__main__":
    main()
