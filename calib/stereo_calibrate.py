#!/usr/bin/env python3
"""Stereo calibration for the CamArray pair — host-agnostic (OpenCV only).

Reads L/R checkerboard image pairs from calib/captures/ (written by
hosts/pi5/camarray_capture.py), detects corners, calibrates each camera and the
stereo rig, rectifies, and writes bench/camarray_stereo.yaml (K/D/R/T/E/F, the
rectification R1/R2/P1/P2/Q, baseline, and RMS errors). Per the target sheet:
findChessboardCornersSB -> stereoCalibrate(FIX_ASPECT_RATIO) -> stereoRectify.

    python3 calib/stereo_calibrate.py --cols 11 --rows 8 --square 33.5 \
            [--captures DIR] [--out bench/camarray_stereo.yaml] [--date YYYY-MM-DD]

--cols/--rows are INNER corners; --square is the measured cell size in mm (a pure
scale factor: it sets the absolute baseline/depth, nothing else — pin it precisely
later with a known-distance check). RMS targets from the sheet: <0.3 px per camera,
<0.5 px stereo. Re-shoot outlier pairs; do not average bad data in.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import cv2
import numpy as np

_SB_FLAGS = (cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY | cv2.CALIB_CB_NORMALIZE_IMAGE)


def make_object_points(cols: int, rows: int, square: float) -> np.ndarray:
    """One board's 3D corner grid (Z=0), row-major, scaled to mm. Pure/tested."""
    objp = np.zeros((rows * cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2).astype(np.float32) * float(square)
    return objp


def _pair_paths(captures: pathlib.Path):
    pairs = []
    for lp in sorted(captures.glob("pair*_L.png")):
        idx = re.search(r"pair(\d+)_L", lp.name).group(1)
        rp = captures / f"pair{idx}_R.png"
        if rp.exists():
            pairs.append((idx, lp, rp))
    return pairs


def detect_pairs(captures, pattern):
    """Detect corners in every L/R pair. Returns (objless imgpointsL, imgpointsR,
    kept indices, dropped indices, image_size)."""
    ptsL, ptsR, kept, dropped, size = [], [], [], [], None
    for idx, lp, rp in _pair_paths(captures):
        L = cv2.imread(str(lp), cv2.IMREAD_GRAYSCALE)
        R = cv2.imread(str(rp), cv2.IMREAD_GRAYSCALE)
        if L is None or R is None:
            dropped.append((idx, "unreadable"))
            continue
        size = (L.shape[1], L.shape[0])
        okL, cL = cv2.findChessboardCornersSB(L, pattern, _SB_FLAGS)
        okR, cR = cv2.findChessboardCornersSB(R, pattern, _SB_FLAGS)
        if okL and okR:
            ptsL.append(cL)
            ptsR.append(cR)
            kept.append(idx)
        else:
            dropped.append((idx, f"L={'ok' if okL else 'miss'} R={'ok' if okR else 'miss'}"))
    return ptsL, ptsR, kept, dropped, size


def _stereo_view_errors(objpoints, ptsL, ptsR, K1, D1, K2, D2, R, T):
    """Per-pair RMS reprojection error in px (left+right combined). A pair whose L/R
    corner ordering is flipped shows up here as a huge value even when the global fit
    looks only moderately bad -- that's what outlier rejection keys on."""
    errs = []
    for op, pL, pR in zip(objpoints, ptsL, ptsR):
        _, rv, tv = cv2.solvePnP(op, pL, K1, D1)
        pjL, _ = cv2.projectPoints(op, rv, tv, K1, D1)
        rv2, _ = cv2.Rodrigues(R.dot(cv2.Rodrigues(rv)[0]))
        pjR, _ = cv2.projectPoints(op, rv2, R.dot(tv) + T, K2, D2)
        dL = np.linalg.norm(pjL.reshape(-1, 2) - pL.reshape(-1, 2), axis=1)
        dR = np.linalg.norm(pjR.reshape(-1, 2) - pR.reshape(-1, 2), axis=1)
        errs.append(float(np.sqrt(np.mean(np.concatenate([dL, dR]) ** 2))))
    return np.array(errs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cols", type=int, default=11)
    ap.add_argument("--rows", type=int, default=8)
    ap.add_argument("--square", type=float, required=True, help="measured cell size in mm")
    ap.add_argument("--captures", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--date", default="", help="capture date YYYY-MM-DD for the record")
    args = ap.parse_args()

    repo = pathlib.Path(__file__).resolve().parents[1]
    captures = pathlib.Path(args.captures) if args.captures else repo / "calib" / "captures"
    out = pathlib.Path(args.out) if args.out else repo / "bench" / "camarray_stereo.yaml"
    pattern = (args.cols, args.rows)

    ptsL, ptsR, kept, dropped, size = detect_pairs(captures, pattern)
    print(f"pairs: {len(kept)} usable, {len(dropped)} dropped  (dir: {captures})")
    for idx, why in dropped:
        print(f"  dropped pair{idx}: {why}")
    if len(kept) < 8:
        print(f"ERROR: only {len(kept)} usable pairs — need ~15+ with varied poses. "
              f"Capture more (near/far, edges, tilted) and re-run.")
        sys.exit(1)

    objp = make_object_points(args.cols, args.rows, args.square)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-6)
    flags = cv2.CALIB_FIX_ASPECT_RATIO | cv2.CALIB_USE_INTRINSIC_GUESS

    # Fit -> flag gross-error pairs (flipped L/R corner order etc.) -> drop -> refit.
    # A few bad pairs blow the stereo RMS from <1px to tens of px while mono stays low.
    rms1 = rms2 = rms = 0.0
    for _pass in range(8):
        objpoints = [objp] * len(kept)
        rms1, K1, D1, *_ = cv2.calibrateCamera(objpoints, ptsL, size, None, None)
        rms2, K2, D2, *_ = cv2.calibrateCamera(objpoints, ptsR, size, None, None)
        rms, K1, D1, K2, D2, R, T, E, F = cv2.stereoCalibrate(
            objpoints, ptsL, ptsR, K1, D1, K2, D2, size, flags=flags, criteria=crit)
        ve = _stereo_view_errors(objpoints, ptsL, ptsR, K1, D1, K2, D2, R, T)
        thr = max(1.0, 3.0 * float(np.median(ve)))
        bad = [i for i in range(len(kept)) if ve[i] > thr]
        print(f"  pass {_pass}: {len(kept)} pairs  mono L={rms1:.3f} R={rms2:.3f}  "
              f"stereo={rms:.3f}px  worst={ve.max():.1f}px")
        if not bad or len(kept) - len(bad) < 12:
            break
        keep = [i for i in range(len(kept)) if i not in set(bad)]
        for i in bad:
            dropped.append((kept[i], f"outlier {ve[i]:.1f}px"))
        ptsL = [ptsL[i] for i in keep]
        ptsR = [ptsR[i] for i in keep]
        kept = [kept[i] for i in keep]
    baseline_mm = float(np.linalg.norm(T))
    fx = float(K1[0, 0])
    print(f"mono RMS: L={rms1:.3f} px  R={rms2:.3f} px   (target < 0.3)")
    print(f"stereo RMS: {rms:.3f} px   (target < 0.5)   [{len(kept)} inlier pairs]")
    print(f"baseline: {baseline_mm:.2f} mm   fx~{fx:.1f} px (scaled by square={args.square} mm)")

    R1, R2, P1, P2, Q, roi1, roi2 = cv2.stereoRectify(
        K1, D1, K2, D2, size, R, T, flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)

    # Red-team the result before we trust any depth from it (CLAUDE.md rule 9).
    warns = []
    if rms1 > 0.3 or rms2 > 0.3:
        warns.append(f"per-camera RMS above 0.3 px (L={rms1:.3f}, R={rms2:.3f})")
    if rms > 0.5:
        warns.append(f"stereo RMS above 0.5 px ({rms:.3f})")
    if len(kept) < 15:
        warns.append(f"only {len(kept)} pairs (<15) — thin coverage")
    for w in warns:
        print(f"  WARN: {w}")

    out.parent.mkdir(parents=True, exist_ok=True)
    fs = cv2.FileStorage(str(out), cv2.FILE_STORAGE_WRITE)
    fs.write("image_width", size[0])
    fs.write("image_height", size[1])
    fs.write("pattern_cols", args.cols)
    fs.write("pattern_rows", args.rows)
    fs.write("square_size_mm", args.square)
    fs.write("num_pairs", len(kept))
    fs.write("rms_left", rms1)
    fs.write("rms_right", rms2)
    fs.write("rms_stereo", rms)
    fs.write("baseline_mm", baseline_mm)
    fs.write("capture_date", args.date)
    for name, mat in (("K1", K1), ("D1", D1), ("K2", K2), ("D2", D2),
                      ("R", R), ("T", T), ("E", E), ("F", F),
                      ("R1", R1), ("R2", R2), ("P1", P1), ("P2", P2), ("Q", Q)):
        fs.write(name, mat)
    fs.release()
    print(f"wrote {out}")
    if warns:
        print("Calibration written but flagged above — consider re-shooting outliers "
              "before ticking this as the rig calibration.")


if __name__ == "__main__":
    main()
