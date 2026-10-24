# OV9281 stereo investigation & reconstruction

Diagnostics, self-calibration, and reconstruction scripts from the 2026-08 OV9281 stereo-SLAM
root-cause (full write-up: [`docs/2026-08-slam-ov9281-progress.md`](../../docs/2026-08-slam-ov9281-progress.md);
provenance in `bench/benchlog.md`). Plain Python/OpenCV + bash. Paths inside the scripts are the
on-bench layout (`/home/garuda/...`); adjust for other machines. SLAM runs use the
`garuda/orbslam3:bench` container on the Pi.

### Diagnostics (why OV9281 stereo "failed")
- `stereo_corr_battery.py` — identical correspondence funnel on EuRoC (control) vs OV9281, and our
  `rectify_clip.py` maps vs a clean `cv2.stereoRectify`. Isolates funnel-code / our-rectification / sensor.
- `dydx_diag.py` — vertical/horizontal offset of good stereo matches after rectification (found the
  113 px off-row + negative disparity → calibration is wrong, not the sensor).
- `recover_pose.py` — recover true relative pose from the images; rectify from it (113 px → 1.3 px).
- `f_uncalib.py` — fundamental-matrix RANSAC (80% inliers ⇒ valid rigid stereo pair).
- `ncc_only.py`, `temporal_vs_stereo.py` — descriptor-independent (NCC) and temporal-vs-stereo checks.
- `perframe_vs_global.py`, `motion_corr.py` — attribute the residual to per-frame geometry vs motion.

### Self-calibration & clip rebuild
- `self_calib.py` — scene self-cal of extrinsics (pool correspondences → essential matrix → numpy
  Gauss-Newton refine → auto L/R swap → rebuild rectified clip + ORB-SLAM3 yaml). **Fit from STILL frames.**
- `build_corrected_clip.py` — earlier image-recovered-extrinsics rebuild (first 1→21 KF proof).
- `build_move.py` — rebuild the still-then-move clip with the **still-fit** calibration (→ 2/207 clean).

### Capture & controlled motion test
- `static_capture.py` — grab N pairs from the stationary OV9281 (same backend/tile-split as
  `hosts/pi5/camarray_record.py`).
- `static_analysis.py` — static vs moving geometry stability + rectified |dy|.
- `move_analysis.py` — controlled still-then-move: classify frames by motion, fit from still, |dy| per bin.

### Reconstruction
- `pointcloud.py` — per-keyframe SGBM depth → 3D → world (by ORB-SLAM3 pose) → point cloud + trajectory.
- `reproject.py` — reproject the cloud back onto real frames (AR-style overlay, depth-colored).

### Misc
- `ov_sweep.sh` — pixel-density → front-end fps sweep (resolution vs tracking cost).
- `make_traj_json.py` — build the SLAM trajectory-viewer data (est aligned to GT + per-pose error).

**Key method note:** self-calibrate OV9281 extrinsics from **still** frames only — a moving clip
inflates the recovered inter-camera rotation by ego-motion (2.4° → 7.5°) and wrecks rectification.
