# garuda-follow — mono subject-following design

**Date:** 2026-07-30 · **Status:** approved in session · **Depends on:** garuda-inference
(detections), the Phase-5 PX4 SITL ↔ ROS 2 bridge, the interfaces message layer.

## Goal

Add subject-following ("follow-me") to the SDK using the single (mono, rolling-shutter)
Camera Module 3: turn per-frame detections into stable tracked targets, select one, and drive
a drone to keep that subject framed — demonstrated end-to-end. Because the bench camera is
static and cannot physically follow a simulated drone, the build has two honest halves that
share one controller:

1. **Real perception path.** Real CM3 → detections (existing) → tracker → target selector →
   follow-controller computes the setpoints it *would* send. Surfaced live in the portal.
2. **Simulated closed loop.** A synthetic subject is projected into the drone's *simulated*
   camera; the same controller drives PX4 SITL offboard setpoints through the Phase-5 bridge;
   the drone converges and holds framing. `GARUDA_SIM=1` gated.

Nothing on this bench arms hardware; all flight is simulated. Perception is real video.

## Non-goals (this increment)

- Metric range/depth (mono gives bearing + a size proxy only — precise range is a ToF /
  GS-stereo item, honestly labelled).
- Re-identification across long occlusions (a track lost past threshold is dropped; a returning
  subject may get a new ID). Noted as a follow-up.
- Obstacle avoidance, multi-target following, or any real-hardware flight.

## Components

All new code lives in `services/garuda-follow/`. The tracker, selector, and controller are
**pure Python (stdlib + numpy-free where practical), no camera/Gst/ROS dependencies**, so they
run identically on the dev PC (tests) and the Pi (live), and are consumed by both the real
path and the SITL harness.

### `tracker.py` — ByteTrack-lite / IoU tracker
- `Tracker(iou_threshold=0.3, max_age=15).update(detections, frame_wh) -> list[Track]`
- Greedy IoU association of new detections to existing tracks; unmatched detections spawn new
  tracks (monotonic integer IDs); unmatched tracks age and are dropped past `max_age`.
- `Track`: `id`, `class_id`, `label`, `bbox` (normalized xywh), `confidence`, `hits`, `age`,
  `time_since_update`.

### `target_selector.py` — which track to follow
- `select(tracks, follow_class="person", pinned_id=None) -> Track | None`
- If `pinned_id` is set and still present, follow it; else auto-lock the highest-confidence
  track whose label == `follow_class`. Returns None if none qualify.

### `follow_controller.py` — image-based visual servoing (pure)
- `compute(target, frame_wh, cfg) -> FollowCommand`
- Errors from the target bbox: horizontal centre offset → **yaw_rate**; bbox-height error vs
  `cfg.desired_height_frac` → **forward_vel** (mono range proxy); vertical centre offset →
  **vertical_vel**. Proportional gains with deadzones and output clamps from `cfg`.
- **Safety gate (load-bearing):** returns a *hold* command (all velocities 0, `valid=False`,
  `reason` set) when `target is None`, `target.confidence < cfg.min_confidence`,
  `target.time_since_update > cfg.max_stale_frames`, or bbox height is outside
  `[cfg.min_height_frac, cfg.max_height_frac]`. This is the VIO-covariance/validity discipline
  applied to following: the loop must never chase a low-confidence, stale, or implausible
  target.

### `sitl_followme.py` — simulated closed loop (`GARUDA_SIM` gated)
- Refuses to run unless `GARUDA_SIM=1`.
- Maintains a synthetic subject at a world position; each step, projects it into the drone's
  simulated camera using the drone pose from PX4 SITL (via the Phase-5 uXRCE/ROS 2 bridge) and
  the camera intrinsics → a bbox; feeds bbox → controller → publishes offboard setpoints;
  loops. Logs the framing error over time and asserts convergence (subject returns to image
  centre at the desired size, and stays).

### `interfaces/garuda_interfaces/messages.py` — new schemas
- `Track` (fields above) and `FollowCommand` (`yaw_rate`, `forward_vel`, `vertical_vel`,
  `valid`, `reason`, `target_id`, timestamp) — transport-neutral, `to_dict()`.

## Portal integration

- The Inference panel's annotated feed gains **track IDs** on the boxes and a small
  **Follow control**: a selector to pin a track ID (or "auto"), and a readout of the live
  `FollowCommand` (yaw/forward/vertical + valid/reason).
- The portal runs the pure tracker + selector + controller on the detection stream it already
  receives from garuda-inference; publishes tracks/command on the existing event channel. No
  new privileged surface; configd/whitelist untouched.

## Testing

- **Pure unit tests** (dev PC, no hardware):
  - tracker: ID persistence across frames, re-spawn after a gap, no ID swap when two tracks
    cross, ageing/drop past `max_age`.
  - selector: auto-pick highest-confidence of class; pinned override; None when nothing
    qualifies.
  - controller: centred target → ~zero command; off-centre → yaw toward centre; small bbox →
    forward, large bbox → back; **every safety-gate branch** → hold with the right reason.
- **Pure closed-loop convergence test:** a simulated target + simple drone kinematics (a few
  lines of pose integration, no PX4) driven by the real controller; assert the framing error
  monotonically shrinks to within a tolerance and stays — fast, deterministic, in CI.
- **Live SITL demo:** run `sitl_followme.py` against the real PX4 SITL + ROS 2 bridge; record
  convergence evidence (framing-error-over-time, final hold) in `bench/benchlog.md`.

## Dependencies & licence

No new third-party dependencies for the pure modules (stdlib). The SITL harness reuses the
existing Phase-5 stack (px4_msgs, the ROS 2 Jazzy container, PX4 SITL). All BSD/Apache — no
GPLv3. Everything authored here is BSD-3 (project policy).

## Safety framing

Subject-following for filming/inspection is standard companion-computer autonomy. This is a
perception + setpoint-generation feature: the companion publishes what it sees and a desired
framing command; the flight controller owns flight; nothing here arms motors, and all flight
is `GARUDA_SIM`-gated simulation. The controller's safety gate ensures a lost or uncertain
target produces a hold, never a chase.
