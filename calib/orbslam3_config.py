#!/usr/bin/env python3
"""Convert our stereo calibration (bench/camarray_stereo.yaml) into an ORB-SLAM3 stereo settings yaml.

ORB-SLAM3's newer format takes RAW per-camera intrinsics + distortion + a single Stereo.T_c1_c2
transform (it rectifies internally), so we feed RAW tiles and map our OpenCV stereoCalibrate output
straight in:  Camera1 <- K1/D1 (tile0),  Camera2 <- K2/D2 (tile1),  T_c1_c2 <- [R | T].

L/R convention: Camera1=left, Camera2=right; T_c1_c2 is cam1->cam2 (our stereoCalibrate R,T:
X2 = R*X1 + T, T in metres). If stereo tracking fails to initialise (bad epipolar direction), swap
tile0/tile1 at the feed side and negate — verify empirically on the first recorded clip.

Needs cv2 (run on the Pi / in the container):
    python3 calib/orbslam3_config.py bench/camarray_stereo.yaml hosts/pi5/orbslam3/garuda_stereo.yaml [fps]
"""
from __future__ import annotations

import sys

import numpy as np


def _m(fs, key):
    return fs.getNode(key).mat()


def to_orbslam3_stereo(calib_path: str, fps: float = 20.0, swap: bool = False) -> str:
    """swap=True flips which tile is Camera1(left)/Camera2(right) and inverts the extrinsic —
    use it (plus swapping the cam0/cam1 image folders) when stereo init triangulates almost
    nothing with the default ordering (tile0 is physically the RIGHT camera on this rig)."""
    import cv2
    fs = cv2.FileStorage(calib_path, cv2.FILE_STORAGE_READ)
    if not fs.isOpened():
        raise SystemExit(f"cannot open {calib_path}")
    W = int(fs.getNode("image_width").real())
    H = int(fs.getNode("image_height").real())
    K1, D1 = _m(fs, "K1"), _m(fs, "D1").ravel()
    K2, D2 = _m(fs, "K2"), _m(fs, "D2").ravel()
    R, T = _m(fs, "R"), _m(fs, "T").ravel()
    fs.release()

    Tm = (T / 1000.0).reshape(3)        # mm -> m
    if swap:
        # Camera1 <- tile1 (K2/D2), Camera2 <- tile0 (K1/D1); extrinsic becomes the inverse.
        K1, D1, K2, D2 = K2, D2, K1, D1
        R, Tm = R.T, -(R.T @ Tm)
    Tc1c2 = np.eye(4)
    Tc1c2[:3, :3] = R
    Tc1c2[:3, 3] = Tm

    def cam(prefix, K, D):
        k = list(D) + [0, 0, 0, 0, 0]
        return (f"{prefix}.fx: {K[0,0]:.8f}\n{prefix}.fy: {K[1,1]:.8f}\n"
                f"{prefix}.cx: {K[0,2]:.8f}\n{prefix}.cy: {K[1,2]:.8f}\n"
                f"{prefix}.k1: {k[0]:.8f}\n{prefix}.k2: {k[1]:.8f}\n"
                f"{prefix}.p1: {k[2]:.8f}\n{prefix}.p2: {k[3]:.8f}\n{prefix}.k3: {k[4]:.8f}\n")

    data_block = ",\n         ".join(",".join(f"{Tc1c2[r, c]:.10f}" for c in range(4)) for r in range(4))
    baseline_m = float(np.linalg.norm(Tm))
    return f"""%YAML:1.0
File.version: "1.0"
# Generated from {calib_path} by calib/orbslam3_config.py — Garuda OV9281 CamArray stereo.
# baseline = {baseline_m*1000:.2f} mm.  BENCH/EVAL (ORB-SLAM3 GPL-3.0, not-product-cleared).

Camera.type: "PinHole"

{cam("Camera1", K1, D1)}
{cam("Camera2", K2, D2)}
Camera.width: {W}
Camera.height: {H}
Camera.fps: {fps:.0f}
Camera.RGB: 1

Stereo.ThDepth: 40.0
Stereo.T_c1_c2: !!opencv-matrix
  rows: 4
  cols: 4
  dt: f
  data: [{data_block}]

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


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "bench/camarray_stereo.yaml"
    dst = sys.argv[2] if len(sys.argv) > 2 else "hosts/pi5/orbslam3/garuda_stereo.yaml"
    fps = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
    swap = len(sys.argv) > 4 and sys.argv[4].lower() in ("swap", "1", "true")
    open(dst, "w").write(to_orbslam3_stereo(src, fps, swap))
    print(f"wrote {dst} (fps={fps:.0f} swap={swap})")
