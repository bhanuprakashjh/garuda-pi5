# garuda-follow — mono subject-following

Licence: **BSD 3-Clause** (see the repository [`LICENSE`](../../LICENSE)) — consistent with
project policy (CLAUDE.md §1), pure Python stdlib, no GPLv3 dependency anywhere in this
module.

Subject-following built on a **single** camera (Camera Module 3, IMX708) — track detections
across frames, pick one to follow, and drive an image-based visual-servo controller toward
it. No metric depth sensor is used or assumed; see "Mono limitation" below.

## Components

| File | Role |
|---|---|
| `tracker.py` | Dependency-free IoU tracker (ByteTrack-lite: greedy IoU association) → persistent track IDs. No re-identification: a track lost past `max_age` is dropped; a reappearing object gets a **new** id (honest mono limitation, not a bug — see Non-goals). |
| `target_selector.py` | `select(tracks, follow_class="person", pinned_id=None)` — the pinned track wins if present (regardless of class), else the highest-confidence track of `follow_class`, else `None`. |
| `follow_controller.py` | `compute(target, frame_wh, cfg, t_ns=0) -> FollowCommand` — image-based visual servoing (yaw-rate from horizontal centering error, forward velocity from bbox-height error, vertical velocity from vertical centering error). Carries the module's **load-bearing safety gate** (below). |
| `projection.py` | World → normalized-image bbox projection for a forward-facing pinhole camera: bearing (horizontal FOV) drives horizontal position, range drives bbox height. `vfov` is accepted but documented unused — this projection is 2D only. Used by `sitl_followme.py` and the test harnesses. |
| `sim_kinematics.py` | A minimal 2.5D drone + world-target simulator (`DroneState`, `step`, `target_bbox`) used to prove the controller converges in a true closed loop with no PX4 involved — deterministic and fast. |
| `sitl_followme.py` | Live PX4-SITL harness. `follow_tick(...)` is the pure, ROS-free control core (importable with no `rclpy`/`px4_msgs` installed); `main()` is the `GARUDA_SIM`-gated rclpy node that actually arms and drives PX4 offboard setpoints. |
| `tests/` | 47 unit/integration tests: tracker, target selector, follow controller (incl. every HOLD path), projection, message-schema round-trips, `sitl_followme`'s pure core, and the closed-loop convergence + reacquisition proof. |

Message schemas (`Track`, `FollowCommand`) live in
[`interfaces/garuda_interfaces/messages.py`](../../interfaces/garuda_interfaces/messages.py) —
transport-neutral dataclasses with a `to_dict()`, per the interfaces contract (CLAUDE.md §1).

Portal integration: [`services/garuda-portal/follow_pipeline.py`](../garuda-portal/follow_pipeline.py)
wraps `Tracker.update → target_selector.select → follow_controller.compute` into one call per
detection frame, defensively dropping malformed detections rather than raising. The portal UI
(`static/index.html` / `static/app.js`, "Inference" panel) draws track IDs on the live
annotated feed, shows a live `FollowCommand` readout (yaw/forward/vertical + a valid/reason
badge), and offers a **"Follow #N"** button per track to pin it, plus an "Auto (unpin)"
control to return to highest-confidence auto-selection.

## The safety gate

`follow_controller.compute()` is deliberately conservative: **any** of the following causes it
to return a `FollowCommand` with `valid=False` and all velocities zero (a HOLD), never a
best-guess chase —

- `target is None` — no track selected ("no target").
- `target.confidence < cfg.min_confidence` (default 0.35) — low confidence.
- `target.time_since_update > cfg.max_stale_frames` (default 5) — stale (tracker hasn't
  matched a detection to this track recently).
- bbox height outside `[cfg.min_height_frac, cfg.max_height_frac]` (default `[0.05, 0.95]`) —
  implausible size.

This is the same discipline as the Phase-5 VIO-freeze finding (`bench/benchlog.md`,
2026-07-30): **gate on the observation's own validity, never on "is something still
arriving."** In `sitl_followme.py`'s live node, a HOLD result still keeps commanding
altitude-hold (see `build_setpoint_fields`) — i.e. the drone hovers in place, it does not fall
or drift, and it never chases a target it cannot see.

## Mono limitation: bearing + size-proxy, no metric depth

This module runs on the single CM3 — there is no stereo pair or ToF sensor behind it yet
(CLAUDE.md §2: the IMX296 GS pair and the Arducam T2 ToF are both still "sourcing"/"planned").
Consequently:

- **Framing = bearing (horizontal position in frame) + bounding-box size as a proxy for
  range.** A bigger bbox means "closer," not a measured distance.
- **No metric depth.** `follow_controller.compute()`'s `forward_vel` is driven by
  desired-vs-actual bbox height, not by a range measurement in metres.
- `projection.py`'s `vfov` parameter is accepted but unused — the projection model is
  horizontal-only (bearing/range), so it has no vertical framing/altitude model of its own.
  `vertical_vel` out of `compute()` is real code (driven by vertical centering error), but in
  the SITL harness the projected bbox never moves vertically, so `vertical_vel` is always 0
  through that path, and altitude is held by a position-Z setpoint instead.
- True vertical/metric-depth framing is a future item for the ToF plugin or the GS stereo
  pair (`interfaces/`'s `IDepthSource`), not this increment.

## How it's proven (three ways)

**1. Pure deterministic closed-loop convergence + reacquisition** — no hardware, no SITL.
`tests/test_convergence.py` drives the real `follow_controller.compute()` through
`sim_kinematics.step()` for 400 ticks (20 s @ 0.05 s/tick), starting off-axis and far from a
world target at `(6.0, 4.0)`. Recorded in `bench/benchlog.md` (2026-07-31, "deterministic
convergence test" note) as a dev-PC/CI unit-test result, not a hardware measurement:
  - Framing error **0.907 → 0.028** (a ~32× reduction), held (`sum(errs[-20:])/20 == 0.028`,
    not a transient dip).
  - A companion test proves a target just outside the FOV is correctly held (not chased), and
    that once external re-acquisition brings it back into view, the same real `compute()` call
    resumes valid tracking: framing error **0.838 → 0.057** over the 100 ticks following
    re-acquisition — a real, measured improvement, not just "doesn't crash."

**2. The portal, live on the CM3 detection stream.** `follow_pipeline.py` + the Inference
panel show real track IDs on the real annotated feed and a live `FollowCommand` readout,
with the "Follow #N" pin control — this is the real perception path (capture → NPU detection
→ tracker → controller), proven on real video, but without flight.

**3. A live PX4-SITL closed loop** (`sitl_followme.py`, `GARUDA_SIM=1` gated) — the real
control-loop half proven against a real running PX4 instance. See `bench/benchlog.md`,
2026-07-31 entry ("garuda-follow: mono follow-me SITL closed loop PASS"):

| Metric | Value |
|---|---|
| Framing error (start → converged) | **0.901 → 0.008**, held < 0.03 for ~50 s to the end |
| Time to framing error < 0.09 | ~11 s (after taking off to the 3 m hold altitude) |
| Drone final pose (NED) | ~(5.1, 3.2) m, alt 3.00 m, yaw ~0.6–0.8 rad — facing/framing the target |
| Commands at convergence | vx/vy → ±0.01 m/s (settled, no chatter) |
| `GARUDA_SIM` gate (no flag) | refuses: `"REFUSED: GARUDA_SIM=1 not set …"`, exit 1 |

Honesty about scope (also in the benchlog entry): the target is a **synthetic** world point
projected through `projection.py`, and the flight is simulated — the drone's static bench
camera cannot physically follow a moving sim drone, so this proves *the control loop closes
and holds against real PX4*, not real perception. The real detector/tracker on live video is
proof #2 (the portal), above — this run and that one are deliberately separate proofs of
separate halves of the pipeline.

Two live-integration fixes surfaced only by running against real PX4 (the pure logic — 32 of
this module's 47 unit tests, including the deterministic convergence + reacquisition proof —
was already green before either fix):
1. Pose is read from `/fmu/out/vehicle_odometry`, not `vehicle_local_position` — this
   firmware build (`47ce421`) publishes the latter under the versioned name
   `vehicle_local_position_v1`, which this container's `px4_msgs` doesn't match.
2. Takeoff + altitude-hold needed a **mixed position/velocity** `TrajectorySetpoint`
   (`build_setpoint_fields`): altitude is position-controlled (`position.z = -hold_alt`),
   horizontal motion + yaw-rate are velocity-controlled from `follow_tick`'s output — a
   velocity-only setpoint (`vz` always 0, since the 2D projection has no altitude model)
   never commands a climb, so the quad just sat armed on the ground.

## Running the SITL demo

This reproduces proof #3 above. It requires the Phase-5 bridge topology already on this
bench (`bench/benchlog.md`, 2026-07-30 entries): PX4 SITL on the development PC, the
Micro-XRCE-DDS agent bridging to a ROS 2 Jazzy container on the Pi over the direct Ethernet
link.

1. **PC side:** start PX4 SITL (`sihsim_quadx`) and set
   `param set EKF2_GPS_CTRL 7` — the P5 failsafe demo leaves this parameter at `0`
   (vision-primary navigation for that demo), and this follow-me harness needs normal
   GPS-referenced navigation, so confirm/reset it to `7` before running.
2. **Bridge:** confirm the Micro-XRCE-DDS Agent session is up between the PC and the Pi (UDP
   8888 over the direct link), per the existing Phase-5 procedure.
3. **Pi side:** enter the `garuda/ros-jazzy` container (`hosts/pi5/Dockerfile.ros-jazzy`,
   `px4_ws` sourced), with `services/garuda-follow/{sitl_followme.py,follow_controller.py,
   projection.py}` and `interfaces/garuda_interfaces/` present at the same relative paths as
   in this repo (the scripts resolve `interfaces/` via `parents[2]`).
4. **Confirm the gate refuses without the flag** (do this once, for the record):
   ```
   python3 sitl_followme.py
   ```
   expect an immediate `REFUSED: GARUDA_SIM=1 not set …` and a non-zero exit — it never
   touches `rclpy`/`px4_msgs`/the vehicle without the flag.
5. **Run the live demo:**
   ```
   GARUDA_SIM=1 python3 sitl_followme.py --duration 60 --hold-alt 3.0
   ```
   Watch the once-per-second log line for `pos N=... E=... alt=...`, `yaw=...`,
   `valid=.../reason=...`, `framing_err=...`, and the commanded `cmd vx=... vy=...
   alt_target=... yawspeed=...`. The run lands automatically (`VEHICLE_CMD_NAV_LAND`) once
   `--duration` elapses.

**Nothing here arms real hardware.** `GARUDA_SIM=1` is required to reach `main()`'s
`rclpy`/`px4_msgs` imports and vehicle commands at all; the only target this harness may ever
command is a SITL/simulated PX4 instance (CLAUDE.md §2 / §6 rule 12).

## Running the tests

```
python3 -m pytest services/garuda-follow -v      # this module only (47 tests)
python3 -m pytest services/ -v                    # whole repo, incl. the portal integration
```

No hardware, no `rclpy`, no `GARUDA_SIM` needed for any test in this module — `sitl_followme`'s
`follow_tick`/`body_to_world_velocity`/`build_setpoint_fields`/`quaternion_to_yaw` are all pure
functions exercised directly.

## Non-goals (this increment)

- **Re-identification across long occlusions.** `tracker.py` has no appearance model; a track
  dropped past `max_age` and later reappearing gets a new id. Explicitly out of scope.
- **Metric depth / true vertical framing.** See "Mono limitation" above — tracked as future
  work behind the ToF (`Arducam T2`) or GS-stereo `IDepthSource` plugin.
- **Search/scan behavior when the target is lost.** The controller only ever holds or tracks;
  driving a search sweep to reacquire a lost target is external to `follow_controller.py` (see
  `tests/test_convergence.py::test_reacquires_when_target_enters_fov`, where the search step is
  test-harness code, not the controller).
