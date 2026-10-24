#!/usr/bin/env python3
"""Rectify a raw CamArray EuRoC clip with our PROVEN stereo_rig maps, and emit a matching
pre-rectified ORB-SLAM3 yaml. We feed ORB-SLAM3 already-rectified stereo instead of trusting
its internal rectification of our raw tiles (which triangulated almost nothing). Same
rectification that makes the SGBM depth path work this session.

    python3 hosts/pi5/rectify_clip.py <raw_clip_dir> <rect_clip_dir> <out_yaml>
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2


def main():
    inp = Path(sys.argv[1])
    outp = Path(sys.argv[2])
    yaml_out = Path(sys.argv[3])

    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "garuda-camera"))                # bench deploy layout
    sys.path.insert(0, str(repo / "services" / "garuda-camera"))   # repo layout
    from stereo_rig import StereoRig

    calib = repo / "bench" / "camarray_stereo.yaml"
    rig = StereoRig(calib)
    fs = cv2.FileStorage(str(calib), cv2.FILE_STORAGE_READ)
    P1 = fs.getNode("P1").mat()
    fs.release()
    fx, fy, cx, cy = float(P1[0, 0]), float(P1[1, 1]), float(P1[0, 2]), float(P1[1, 2])
    b = rig.baseline_mm / 1000.0                      # metres

    c0i, c1i = inp / "mav0/cam0/data", inp / "mav0/cam1/data"
    c0o, c1o = outp / "mav0/cam0/data", outp / "mav0/cam1/data"
    c0o.mkdir(parents=True, exist_ok=True)
    c1o.mkdir(parents=True, exist_ok=True)

    names = sorted(p.name for p in c0i.glob("*.png"))
    for nm in names:
        t0 = cv2.imread(str(c0i / nm), cv2.IMREAD_GRAYSCALE)
        t1 = cv2.imread(str(c1i / nm), cv2.IMREAD_GRAYSCALE)
        rc1, rc2 = rig.rectify(t0, t1)
        left, right = rig.order_for_matching(rc1, rc2)   # correct L/R (proven for SGBM)
        cv2.imwrite(str(c0o / nm), left)
        cv2.imwrite(str(c1o / nm), right)
    (outp / "times.txt").write_text((inp / "times.txt").read_text())

    def cam(p):
        return (f"{p}.fx: {fx:.8f}\n{p}.fy: {fy:.8f}\n{p}.cx: {cx:.8f}\n{p}.cy: {cy:.8f}\n"
                f"{p}.k1: 0.0\n{p}.k2: 0.0\n{p}.p1: 0.0\n{p}.p2: 0.0\n{p}.k3: 0.0\n")

    yaml_out.write_text(f"""%YAML:1.0
File.version: "1.0"
# PRE-RECTIFIED CamArray stereo (rectify_clip.py). Images already rectified via stereo_rig;
# both cameras share intrinsics, zero distortion, T_c1_c2 = pure baseline. BENCH/EVAL.
Camera.type: "PinHole"

{cam("Camera1")}
{cam("Camera2")}
Camera.width: 1280
Camera.height: 800
Camera.fps: 20
Camera.RGB: 1

Stereo.ThDepth: 40.0
Stereo.T_c1_c2: !!opencv-matrix
  rows: 4
  cols: 4
  dt: f
  data: [1.0,0.0,0.0,{b:.10f},
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
""")
    print(f"rectified {len(names)} pairs -> {outp}  (fx={fx:.1f} baseline={b*1000:.2f}mm) yaml -> {yaml_out}")


if __name__ == "__main__":
    main()
