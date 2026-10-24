# Research: detection+tracking, distance, obstacle avoidance, follow-target (Pi 5 + Hailo-10H)

**Date:** 2026-08-04 · Four parallel research streams for the "detect → track → range → avoid → follow" use
case. Feeds the S1 design (`docs/superpowers/specs/2026-08-04-garuda-track-detect-lock-design.md`) and the
S2–S4 sub-projects. **Every throughput/accuracy number below is vendor- or doc-reported, NOT measured on this
bench** unless it cites `bench/benchlog.md`; treat all as `TBD-bench` until a harness run confirms them.

Platform framing: "GPU-centric" on this box = the **Hailo-10H NPU** (40 TOPS) — there is no CUDA/discrete GPU.
The inner control loop lives on a separate PX4 FC; the companion does perception + high-level behavior only.

## 1. Detection + tracking (NPU)

- **Detector:** Hailo YOLO family runs well; YOLOv8m is **75.95 fps measured on this bench** (benchlog P1) and
  matches Hailo's own 76.2 fps figure. YOLOv11s/m are a Pareto improvement per Hailo's numbers (re-measure
  before relying on it). Hailo maintains an `ultralytics` fork, so v8/v11 HEF export is well-trodden.
- **Tracker:** Hailo ships a native in-pipeline **`hailotracker`** — a JDE-style tracker (Kalman + IoU, with an
  optional appearance-embedding term), **LGPL-2.1** (license-clean). Standalone options: **ByteTrack (MIT)**,
  OC-SORT (MIT), BoT-SORT (MIT), Norfair (BSD). Avoid DeepSORT's reference impl (GPLv3).
- **"Lock one of many":** no Siamese/SOT model exists on Hailo — the ecosystem pattern (e.g. the **TAILO**
  Pi5+Hailo community project) is **MOT + a target-selection + lock state machine**, which also feeds
  obstacle avoidance from the same detections. Re-acquire short losses via the tracker's lost buffer; long
  losses via an appearance re-ID model (OSNet/RepVGG-ReID on Hailo — **person-only**, Market1501-trained).
- **Decision (S1):** YOLOv8m + ByteTrack (Python, vendored, MIT) + TAILO-style lock FSM; re-ID deferred.
- Sources: Hailo Model Zoo HAILO10H object-detection + person-reID docs; `hailo-apps-core` (`hailo_tracker.cpp`);
  `hailo-rpi5-examples`/TAILO; ByteTrack/OC-SORT/BoT-SORT/Norfair repos.

## 2. Distance to the tracked object

- **Primary: stereo triangulation on the OV9281 pair** (`Z = B·f/disparity`), metric from geometry alone.
  CPU `StereoSGBM` (OpenCV, Apache-2.0) is the zero-NPU baseline. 47 mm baseline ⇒ cm-accurate at 1–5 m,
  error grows **quadratically** (`ΔZ ∝ Z²`) toward decimetre–metre near 10 m (physics, to be bench-measured).
- **Cross-sensor problem (color detector → mono depth):** needs a **3-camera extrinsic calibration**
  (IMX708 ↔ OV9281 pair, e.g. Kalibr/`multical`), with the rig built so the color cam's FOV overlaps ≥1 mono
  cam. Then project the color-frame bbox into the rectified stereo depth map and sample a robust median. This
  is **P0**, a prerequisite for S2/S3 and needs a rigid mount.
- **Monocular depth NNs on Hailo** (`fast_depth`, `scdepthv3` = NYU-indoor semi-metric; `depth_anything` =
  relative-only) are cheap on the NPU but **not reliable metric** for this domain — secondary signal /
  gap-filler only, not primary.
- **Hailo StereoNet** exists (KITTI-trained, 368×1232 input, ~16.7 fps reported on Hailo-10H over PCIe) but
  domain/aspect mismatch + a suspicious param count ⇒ evaluate later, don't commit blind.
- **Fallback:** bbox + known-object-size ranging (color cam only) for out-of-stereo-range or as a cross-check.
- **Decision (S2):** stereo SGBM primary + cross-cam calibration; monocular/bbox-size as fallback/secondary.
- Sources: OV9281 datasheet; OpenCV depth-map docs; RealSense tuning (Z² error); Hailo Model Zoo depth/stereo
  docs; Kalibr multi-cam wiki.

## 3. Obstacle avoidance (companion → PX4)

- **Interface:** PX4 **Collision Prevention** consumes **`ObstacleDistance`** (72 sectors × 5°, cm,
  body-frame), published on `/fmu/in/obstacle_distance` over **uXRCE-DDS** (native to the committed ROS 2
  Jazzy setup) — or the field-identical MAVLink `OBSTACLE_DISTANCE` (id 330). PX4 wants **sectors, not point
  clouds / occupancy grids**.
- **Build the sector array** from stereo depth via a `depthimage_to_laserscan`-style per-sector min-reduction;
  fold in dynamic detections (depth-lookup each box → sector) by **nearest-wins**. Leave out-of-FOV sectors as
  "no data" (`CP_GO_NO_DATA=0` blocks motion there — the safe default).
- **Avoid dead ends:** PX4's old **Path Planning Interface** and the **PX4-Avoidance** repo are
  **deprecated/archived** — do not build on them. Map-based/global avoidance would need a custom PX4 ROS 2
  mode + a stable pose source (Phase-4 VIO) → future work, not first delivery.
- **⚠ Critical (see §4):** PX4 Collision Prevention only applies to **manual Position mode**, NOT Offboard —
  so for a vision-driven follow, avoidance must be done on the companion.
- **Decision (S3):** publish `ObstacleDistance` (for the manual safety net + telemetry) AND feed the same
  obstacle picture to the companion-side avoidance arbiter (S4).
- Sources: PX4 Collision-Prevention / ObstacleDistance / Path-Planning(deprecated) docs; `dds_topics.yaml`;
  MAVLink common.xml; `depthimage_to_laserscan`.

## 4. Follow target (companion → PX4)

- **Use PX4 Offboard**, not Follow-Me. Follow-Me needs a global target position + MAVLink (input not on the
  DDS bridge) and has no avoidance. Offboard `TrajectorySetpoint` (velocity + yawspeed; `NaN` = don't control
  that axis) over uXRCE-DDS is the right path; stream ≥ ~2 Hz (10 Hz reference), `COM_OF_LOSS_T` (0.5 s)
  failsafe.
- **No gimbal** (CM3 rigid) ⇒ **yaw-to-center** (pixel error → yaw-rate) + **range-hold** (range error →
  body-forward velocity). Filter target state (bearing + range) with a Kalman/alpha-beta estimator that tracks
  staleness + covariance — analogous to PX4's `landing_target_estimator`.
- **⚠ Verified in PX4 source:** `CollisionPrevention` is linked only into ManualPosition/stick tasks;
  `mc_pos_control` (which executes Offboard setpoints) has **zero** references to it. So **avoidance-vs-follow
  arbitration lives entirely on the companion**, upstream of the published setpoint (hard-stop / soft-blend /
  pass-through). Geofence + RC-loss failsafe remain independent FC-side layers.
- **Safety FSM:** `TRACKING → COASTING → LOST → HOLD`, gated on detection confidence + range validity +
  staleness (never on link liveness — same philosophy as the VIO covariance failsafe). Everything behind
  **`GARUDA_SIM`**: refuse arm/offboard `VehicleCommand` and refuse a real-FC DDS endpoint unless
  `GARUDA_SIM=1` or an explicit supervised bench flag.
- **Decision (S4):** Offboard velocity+yawspeed over DDS; companion-side avoidance arbiter; safety FSM;
  sim-run now, interfaces designed for the real CA90/PX4 later.
- Sources: PX4 Offboard / Follow-Me / Precision-Landing / Safety docs; PX4 source (`CollisionPrevention`,
  `mc_pos_control`, `AutoFollowTarget`, `dds_topics.yaml`); TrajectorySetpoint/OffboardControlMode msg docs.

## Cross-cutting takeaways

1. **Perception on the companion, control split to the FC** — but the FC's reactive avoidance does **not**
   cover Offboard, so the companion owns follow+avoid blending. This is the single most architecture-shaping
   finding.
2. **Stereo is the metric-depth backbone**; NPU depth nets are secondary. The color↔mono **calibration (P0)**
   gates distance + obstacle fusion.
3. **License-clean:** YOLO/ByteTrack/OpenCV/px4_msgs/PX4 are MIT/BSD/Apache/LGPL — no GPL needed for this
   workstream (unlike the ORB-SLAM3 hybrid-SLAM track).
4. **Thermal budget is the recurring risk** — detection + stereo + (later) VIO concurrent on a passive Pi 5 is
   the Phase-6 load gate; measure, don't assume.
