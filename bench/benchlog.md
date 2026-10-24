# bench/benchlog.md — measured results only

Rules: every number here comes from a harness/command actually run on this bench, with date
and a matching `versions.md` snapshot. No estimates. Unmeasured = `TBD-bench`.

---

## 2026-07-31 — garuda-follow: mono follow-me SITL closed loop PASS

`services/garuda-follow/sitl_followme.py` (`GARUDA_SIM=1` gated) run in the ROS 2 Jazzy
container on the Pi against PX4 SITL (`sihsim_quadx`) on the PC over the Phase-5 uXRCE bridge.
A **synthetic** subject at world (6, 4, −2) m is projected into the drone's simulated camera;
the real `follow_controller.compute` drives offboard setpoints (mixed: altitude-hold on Z,
follow velocity on X/Y, yaw-rate for heading). The **real perception path** (CM3 → tracker →
controller command) is proven separately in the portal — not claimed here.

| Metric | Value |
|---|---|
| Framing error (start → converged) | **0.901 → 0.008** (held < 0.03 for ~50 s to end) |
| Time to framing error < 0.09 | ~11 s (took off to 3 m hold altitude first) |
| Drone final pose (NED) | ~(5.1, 3.2) m, alt 3.00 m, yaw ~0.6–0.8 rad — facing/framing the target |
| Commands at convergence | vx/vy → ±0.01 m/s (controller settles, no chatter) |
| `GARUDA_SIM` gate (no flag) | refuses: "REFUSED: GARUDA_SIM=1 not set …", exit 1 |

**Scope / honesty (rule 1 + rule 9):**
- Synthetic target, simulated flight — the drone's static bench camera cannot physically
  follow a moving sim drone, so this proves the *control loop closes and holds*; the *real
  detector/tracker on live video* is the portal's job.
- Mono: framing = bearing (yaw) + bbox-size proxy for range. No metric depth; `vz` from the
  2D projection is a no-op, so altitude is held by a position-Z setpoint, not vision. True
  vertical framing needs ToF or the GS stereo pair.
- Two live-integration fixes surfaced only by running against real PX4 (the pure logic —
  32 unit tests incl. a deterministic convergence + reacquisition proof — was already green):
  (1) pose read from `/fmu/out/vehicle_odometry` because this firmware (47ce421) publishes
  `vehicle_local_position` under the versioned name `vehicle_local_position_v1` that the
  container's `px4_msgs` doesn't match; (2) takeoff + altitude-hold via a mixed
  position/velocity setpoint (a velocity-only follow controller assumes an already-flying
  drone). Also: `EKF2_GPS_CTRL` had persisted to 0 from the P5 failsafe demo — reset to 7.
- Safety: nothing arms real hardware; `GARUDA_SIM` gated; a lost/invalid target commands a
  hover (altitude-hold, zero horizontal velocity), never a chase — the covariance/validity
  discipline from the P5 VIO-freeze finding, applied to following.

### Same day — deterministic convergence test (dev PC, not a hardware measurement)

`services/garuda-follow/tests/test_convergence.py` drives the **real**
`follow_controller.compute()` against a minimal drone+world-target simulator
(`sim_kinematics.py`) — no PX4, no hardware, fully deterministic. This is a unit test
(reproducible via `pytest services/garuda-follow/tests/test_convergence.py -v` on any dev PC
or in CI), not a bench measurement; recorded here per rule 1 so every number cited in docs
traces to this log. Provenance: the test file itself (see also
`.superpowers/sdd/2026-07-30-garuda-follow/task-5-report.md`, which first recorded these
figures).

| Test | Metric | Value |
|---|---|---|
| `test_follow_loop_converges_and_holds` | Framing error, start → end (400 ticks, 20 s sim time) | **0.907 → 0.028** (`errs[0]=0.9068`, `errs[-1]=0.0280`; tail avg over last 20 ticks = 0.0280 — held, not a transient dip) |
| `test_reacquires_when_target_enters_fov` | Framing error, on re-acquisition → after 100 more real-controller ticks | **0.838 → 0.057** (`err0=0.8381`, `err1=0.0567`) |

Scope: both numbers come from stdlib-deterministic arithmetic (no randomness), so re-running
the test reproduces them exactly — this is disclosed as dev-PC/CI unit-test math, distinct
from the SITL entry above (a real PX4 run) and from any hardware benchmark elsewhere in this
log.

---

## 2026-07-29 — P1: NPU sanity (partial)

Versions snapshot: `versions.md` 2026-07-29 Trixie entry.

| Item | Result |
|---|---|
| `hailortcli fw-control identify` | **PASS** — Device Architecture HAILO10H, FW 5.1.1 (release,app) |
| `lspci` Hailo enumeration | PASS — `0001:01:00.0 Co-processor: Hailo Technologies Ltd. Device 45c4 (rev 01)` |
| `vcgencmd get_throttled` at identify time | `0x0` (idle) |
| Model-zoo detection fps on sample video | **PASS — 75.95 fps** (see video-pipeline subsection) |
| LLM prompt via hailo-ollama | **PASS** (see LLM subsection below) |

Caveats (rule 9 red-team, to resolve before ticking the P1 gate):
- Kernel 6.18.34 + h10 5.1.1 from the same repo snapshot — coupling currently consistent;
  re-verify identify after any kernel change (rule 11).

### Same day, later — NPU benchmark on charger power

Power provenance: the first run below predates user confirmation of the charger swap — its
source is uncertain (likely still PC USB-C; it held `0x0` and 5.04 V regardless). The run was
repeated on **confirmed USB-C PD charger** power (model/wattage unrecorded): **FPS 76.24**,
temp mean 53.7 °C / max 59.5 °C, post-load `throttled=0x0`, rail 5.049 V — consistent across
both sources. Identify/install earlier ran on PC USB-C power (trivial load, `0x0` throughout).

| Measurement | Value |
|---|---|
| `hailortcli benchmark /usr/share/hailo-models/yolov8m_h10.hef` | **FPS: 76.23** |
| NPU temperature during run | mean 53.4 °C, max 59.5 °C |
| `get_throttled` after run | `0x0` — no under-voltage/throttle events since boot, incl. during load |
| 5 V rail (`pmic_read_adc EXT5V_V`) after run | 5.041 V |
| On-device power measurement | not supported on this device |

**Scope note (rule 1):** 76.23 fps is NPU streaming throughput on synthetic inputs — NOT the
"sample video" end-to-end number the §5 gate asks for (that includes decode + pre/post). The
gate stays unticked until a video-pipeline run is measured.

### Same day, later — LLM via hailo-ollama (P1 item PASS)

Setup: `hailo-gen-ai-model-zoo` 5.1.1 deb (dev-public.hailo.ai, 7.6 MB), `hailo-ollama`
server on :8000, model `qwen2:1.5b` pulled (1.68 GB, sha256-verified). Charger power.

| Measurement | Value |
|---|---|
| Prompt | "In one sentence, what is a Kalman filter?" |
| Response | coherent, correct one-sentence definition — PASS |
| `eval_count` | 39 tokens |
| `total_duration` (server-side) | 5.13 s |
| Wall time incl. first model load to NPU | 14.75 s |

Note: server reports no separate `eval_duration`, so tokens/s is not cleanly derivable —
raw values recorded instead. Catalog also offers `deepseek_r1_distill_qwen:1.5b`,
`llama3.2:3b`, `qwen2.5-coder:1.5b`, `qwen2.5-instruct:1.5b`.

### Same day, later — end-to-end video pipeline (P1 gate measurement)

Pipeline (on-Pi, GStreamer/tappas): `filesrc detection0.mp4 (Hailo example street clip,
2.3 MB, ~25 s @30 fps) → decodebin → videoconvert/scale → RGB 640×640 → hailonet
yolov8m_h10.hef → hailofilter libyolo_hailortpp_post.so → fakesink` — decode + preprocess +
inference + postprocess all included. Charger power.

| Run | Result | Verdict |
|---|---|---|
| 1st: sink sync enabled (mistake) | avg 30.09 fps, current flat 30.00 | DISCARDED — measured the file's playback clock, not capacity |
| 2nd: sync=false | avg 76.85 fps, 0 drops | TAINTED — `get_throttled=0x50000` sticky (UV + throttle occurred somewhere since boot) |
| 3rd: fresh boot, sync=false | **avg 75.95 fps, 0 drops**, flags `0x0` before AND after, rail 5.040 V | **COUNTED — gate PASS (≥30 required, 2.5× margin)** |

End-to-end ≈ NPU-only (75.95 vs 76.24) ⇒ NPU-bound; A76 decode/pre/post keeps up at this clip size.

**Rule 9 red-team (strongest case the result is wrong, addressed):**
- *Thermally throttled?* Flags `0x0` pre/post counted run; comparable NPU load peaked 59.5 °C.
- *Underpowered?* Real concern — run 2's boot window logged a genuine UV event (`0x50000`) on
  this unverified PD charger. Addressed by reboot + clean re-run (`0x0`→`0x0`). Constraint
  stands: obtain/verify a true 5 V/5 A supply before P6 concurrent-load work.
- *Clock-sync artefact?* Run 1 was exactly that — caught (avg == file fps, current flat 30.00)
  and discarded; counted run is 2.5× file rate, incompatible with clock-lock.
- *Cherry-picked input?* One ~640p 30 fps clip. Sufficient for the P1 sanity gate as written;
  NOT evidence for 1080p/other codecs (decode cost rises). Untested territory noted.
- *Anything else running?* Fresh boot; hailo-ollama server not running (died with reboot).

**P1 gate: TICKED 2026-07-29.**

---

## 2026-07-29 — P2 start: CM3 first light

CM3 (IMX708, module ID 0x0301) detected on CAM/DISP 0 after cable debug. Root cause of
initial non-detection: cable orientation/seating (forced `dtoverlay=imx708` probe showed I2C
`-5` no-ACK on both ports until a correctly-oriented, fully-seated attach; then clean probe).
Diagnostic overlays removed after success; detection re-verified via stock `camera_auto_detect=1`.

| Item | Result |
|---|---|
| `rpicam-hello --list-cameras` | imx708, modes 1536×864@120.13 / 2304×1296@56.03 / 4608×2592@14.35 (fps as reported by libcamera) |
| `rpicam-still` 4608×2592 capture | PASS — real image verified on PC |
| Remaining P2 gate items | GS pair trigger/jitter/soak — blocked on IMX296 hardware |

### 2026-07-30 — CM3 delivered-frame metrics (harness: `bench/cm3-stream-metrics.sh`)

30 s per mode, raw YUV420 → /dev/null (no encode), PTS-trace analysis, drop = gap >1.5×
frame period. `throttled=0x0` before and after the full run.

| Mode | Frames | Delivered fps | Drops | Max gap |
|---|---|---|---|---|
| 1536×864 @120 | 3581 | 120.12 | 0 | 8.33 ms (=1 period) |
| 2304×1296 @56 | 1670 | 56.02 | 0 | 17.86 ms (=1 period) |
| 4608×2592 @14 | 412 | 14.00 | 0 | 71.41 ms (=1 period) |

Max gap equal to exactly one nominal frame period in every mode ⇒ zero delivery jitter
beyond frame quantisation in these windows. Scope: 30 s windows, idle bench — the P2 1-hour
no-drop soak remains a separate, longer test (and its gate wording targets the GS pair).

---

## 2026-07-30 — P4/P5 groundwork: ROS 2 Jazzy container + cross-boundary DDS PASS

`garuda/ros-jazzy` built on the Pi from committed `hosts/pi5/Dockerfile.ros-jazzy`
(never interactively modified — decisions.md rule). Proof: talker and listener in two
separate containers, `--network=host --ipc=host`, CycloneDDS both ends — listener received
from message 1 (no discovery loss), steady 1 Hz. Foundation for PX4 SITL bridge work.

### Same day, later — P5 bridge topology PASS: PX4 SITL (PC) ↔ ROS 2 (Pi) over Ethernet

Faithful dress rehearsal of the real topology (FC is a separate box across a cable):
PX4 SITL + SIH quad on the PC, uXRCE-DDS client → Micro XRCE-DDS Agent in container on the
Pi (UDP 8888 over the direct link), px4_msgs decoding in the Jazzy container.

| Item | Result |
|---|---|
| XRCE session establishment | PASS — client 10.42.0.1 connected ~1.5 s after agent start |
| `/fmu` topics visible on Pi | **69** (out + in, incl. the visual-odometry inputs P5 needs) |
| Typed echo `/fmu/out/vehicle_attitude` | PASS — live SIH quaternion decoded via px4_msgs |

Next (task 4): publish VehicleOdometry → `/fmu/in/vehicle_visual_odometry`, confirm EKF2
fusion, then the covariance-failsafe demo (the actual P5 gate).

### Same day, later — P5 odometry → EKF2 external-vision fusion PASS

`bench/ev_odom_pub.py` (rclpy, 30 Hz stationary NED pose, 0.01 m² position variance,
quality 100) in the Jazzy container on the Pi → `/fmu/in/vehicle_visual_odometry` →
SITL on PC with `EKF2_EV_CTRL=3` (h+v position).

| Check | Result |
|---|---|
| `px4-listener vehicle_visual_odometry` | message arriving ~20 ms fresh, fields intact |
| uXRCE timesync | PASS — ROS timestamps translated into PX4 clock domain |
| `estimator_status_flags` | **`cs_ev_pos: True`, `cs_ev_hgt: True`** (yaw/vel False as configured) |

Remaining for the P5 gate: the VIO-kill → covariance-failsafe demonstration (scripted
scenario with the vehicle armed in simulation), and eventually the same pipeline against
the real CA90. All motion is SITL-simulated on the PC; nothing on this bench arms hardware.

### 2026-07-30 — accidental finding: confidently-wrong EV feed diverges EKF2 (kilometre-scale)

During an offboard flight demo (1 km circuit), the earlier stationary EV publisher was left
running: 30 Hz "position = origin, variance 0.01, quality 100" while GPS reported real motion.
Result: estimator divergence — `vehicle_local_position` reached z = **+1013 m** (a kilometre
below home) with commanded altitude never past 120 m, horizontal estimate pinned near origin.
Recovered by stopping the stale publisher and restarting SITL (fresh estimator).

**Why this matters (P5 failsafe evidence):** a stale-but-confident VIO stream is worse than a
dead one — it doesn't trigger timeout-based failsafes and actively poisons the filter. This is
precisely why the architecture gates failsafe on **odometry covariance/validity, not link
liveness** (CLAUDE.md §1). The covariance-failsafe demo must include this scenario: not just
VIO-kill, but VIO-freeze (last pose repeated with unchanged covariance).

### 2026-07-30 — P5 failsafe scenarios (SITL, vision-primary navigation)

Setup: SITL restarted **vision-primary** (`EKF2_GPS_CTRL=0`, `EKF2_EV_CTRL=3`,
`EKF2_HGT_REF=0` baro). `bench/failsafe_demo.py` on the Pi is the sole position source
(echo-EKF healthy feed, 20 Hz); offboard setpoints stream throughout, so reactions are
attributable to vision alone. Sabotage at t=25 s mid-circuit, observation to t=60 s.

| Scenario | PX4 reaction | Verdict |
|---|---|---|
| **VIO-kill** (feed stops) | `Failsafe: blind land` + `Failsafe activated` within seconds; controlled descent 15 m→ground; disarm | **CAUGHT** — FC-side timeout works for dead feeds |
| **VIO-freeze** (stale pose, fresh timestamps, covariance unchanged) | **No failsafe for the full 35 s window** — nav_state stayed OFFBOARD, estimate pinned at freeze point while setpoints marched on; physical vehicle dragged away (est. read 1.5 km out, 30–50 m/s, once the feed finally died and dead-reckoning unwound); failsafe only fired when the script exited and freeze degraded into kill | **NOT CAUGHT** — FC-side liveness detection is provably insufficient |

**Architecture consequence (the actual finding):** the FC cannot detect frozen VIO on its
own — staleness/degradation detection MUST live on the companion. The `garuda-vio` failsafe
contract: detect own tracking degradation/staleness, inflate reported covariance (or mark
invalid / stop publishing) so the FC's covariance gate fires. This scenario pair becomes the
permanent regression harness for that contract. P5 gate itself stays unticked — its wording
requires the real CA90; the SITL half of the phase is complete.

### 2026-07-30 — P4 groundwork: Basalt VIO baseline on Pi 5 (TUM-VI room1)

Full details + provenance: `datasets/baselines/tumvi_room1_pi5_basalt.md`. Headline: **2821
frames in 46.3 s = 61.0 fps (3.4× real-time), RMS ATE 0.1104 m**, flags `0x0` before/after
(reboot-cleaned; the -j2 build had tripped `0x50000` on the PD charger — third UV strike for
this supply). Per-stage timings TBD-bench.

**EuRoC MH_01 (same day, after ETH outage workaround): RMS ATE 0.0783 m vs published
desktop 0.07 m = 1.12× — the plan's "within 2×" criterion PASSES.** 43.0 fps / 2.1×
real-time, flags `0x0`→`0x0`. The P4 phase gate itself (live ≥20 Hz front end, ≥200 Hz pose,
<20 ms latency) awaits the GS camera pair.

### 2026-07-30 — live camera → NPU detection PASS (garuda-inference seed)

`rpicam-vid --post-process-file hailo_yolov8_inference.json` (auto-selected
`yolov8m_h10.hef` on HAILO10H): live CM3 stream, YOLOv8m on-NPU, boxes drawn.
Verified detection: **person 85%** on a frontal subject. Delivered 291 frames / 20 s at
1536×864 @15 fps requested ⇒ ≈14.6 fps, no under-rate from inference load (NPU has ~5×
headroom per P1). Findings: the hailo postproc stages run only on the **video** pipeline —
`rpicam-still` loads them but inference never engages ("HailoRT not ready!", no boxes);
a hard scene (inverted monitor, cable clutter) yields zero detections while the pipeline is
healthy — absence of boxes is not evidence of a dead pipeline. Next for `garuda-inference`:
structured detections (class/bbox/conf on the middleware) instead of drawn pixels.

---

## 2026-07-29 — P3 start: first PTP offset numbers (point-to-point, SW-timestamped GM)

Topology: PC (`ptp4l -i eno1 -S -m`, **software timestamps** — NIC has no PHC) as
grandmaster ↔ direct cable ↔ Pi `eth0` (linuxptp 4.2, hardware timestamps,
`--hwts_filter=full`, slave-only, transient unit `garuda-ptp4l-client`).
Harness: `bench/ptp-offset-stats.sh`, 60 s servo settle excluded, then 300 s window.

| Measurement | Value |
|---|---|
| Samples | 304 (1/s sync) |
| Mean offset | **−8.8 ns** |
| RMS offset | **3595 ns (3.60 µs)** |
| Min / Max | −8907 ns / +7644 ns |
| Path delay (steady) | ~20.8 µs |

Caveats (structural, known before measuring):
- GM end is software-timestamped — its jitter dominates; this number is NOT the Pi's
  hardware-PTP capability and does NOT test the sub-µs point-to-point target. That waits
  for a HW-timestamping peer (LAN966x TSN switch EVB or CA90).
- RP1 quirk (symptom→fix, rule 4): RX hardware filter modes are `none|all` only; ptp4l's
  default PTP-specific filter request leaves SYNC unstamped ("received SYNC without
  timestamp"). Fix: `--hwts_filter=full`. Must carry into garuda-time config.

---

## 2026-07-30 — garuda-portal V1 deployed to bench

Versions snapshot: `versions.md` 2026-07-29 Trixie entry (2026-07-30 addendum: python3-opencv,
portal/configd units). `hosts/pi5/install.sh` `[5/5]` step deployed `/opt/garuda/{garuda-portal,
garuda-configd,garuda-inference}` from `services/` (staged via rsync to
`garuda@10.42.0.128:/home/garuda/garuda-sdk-deploy/`), installed
`hosts/pi5/units/garuda-{portal,configd}.service`, and replaced the bench transient unit
`garuda-cam-portal` (same camera, same `:8080`) with `garuda-portal.service`.

### Readiness report (`GET /api/readiness/report`, first run after deploy)

| Check | Status | Evidence |
|---|---|---|
| `npu.identify` | pass | `identify: HAILO10H` |
| `power.flags` | pass | `get_throttled=0x0` |
| `power.supply5a` | warn | supply not verified 5V/5A (bench charger has prior UV strikes) |
| `thermals` | pass | SoC 45.0 C |
| `services.core` | warn | `garuda-inference=down, garuda-cam-portal=down` |
| `storage.space` | pass | 222.1 GB free |
| `bridge.session` | na | no FC endpoint configured |
| `simgate.status` | pass | portal sends no arming commands (by design) |

**Overall: caution** — `services.core` warns because `garuda-inference` is on-demand (not a
persistent unit; started/stopped by `InferenceManager` on request) and the old
`garuda-cam-portal` no longer exists (by design, replaced). `power.supply5a` is the permanent
bench advisory and does not by itself demote overall status per the readiness rule.
`bridge.session` reads `na` even though `bridge.agent_port` was set below — the check keys
on `bridge.agent_ip`, which was not set in this session (correct per `readiness.py` logic,
not a bug).

### E2E checklist (curl from the PC against `http://10.42.0.128:8080`)

| # | Check | Result |
|---|---|---|
| 1 | `GET /` | **200**, body contains all seven `id="panel-*"` (overview, camera, inference, calibration, readiness, settings, system) |
| 2 | `GET /api/health` | **200** — `{"throttled": "0x0", "temp_c": 43.3, "npu": "HAILO10H", "services": {"garuda-inference": false, "garuda-cam-portal": false}, "disk_free_gb": 222.1, "camera_mode": "stream", ...}` |
| 3 | `GET /api/stream` | first 200 bytes contain `--frame` boundary + JPEG magic `ffd8ff` — camera confirmed live (rpicam-vid child under the portal) |
| 4 | `GET /api/models` | **200** — `["resnet_v1_50_h10.hef", "yolov11m_h10.hef", "yolov5n_seg_h10.hef", "yolov8m_h10.hef", "yolov8m_pose_h10.hef", "yolov8s_pose_h10.hef"]` (contains `yolov8m_h10.hef`) |
| 5 | `PUT /api/config {"key":"bridge.agent_port","value":8888}` | `{"ok": true, "detail": "ok", "result": {"bridge.agent_port": 8888}}`; `GET /api/config` confirms `{"bridge.agent_port": 8888}` |
| 6 | `PUT /api/config {"key":"evil.key","value":1}` | `{"ok": false, "detail": "config key not whitelisted: 'evil.key'", "result": null}` |
| 7 | `systemctl is-active garuda-portal garuda-configd` | `active` / `active` |
| 8 | `journalctl -u garuda-configd` audit trail | `ACCEPT {"op":"config.set",...bridge.agent_port...} :: set`, `ACCEPT {"op":"config.get",...} :: get`, `REJECT {"op":"config.set",...evil.key...} :: config key not whitelisted: 'evil.key'` |

**8/8 E2E checks green.** Full command+output transcript:
`.superpowers/sdd/2026-07-30-garuda-portal-v1/task-9-report.md`.

### Idempotency re-run (install.sh run a second time, same session)

Re-ran `hosts/pi5/install.sh` on the Pi unchanged. Result: apt step reported
"Upgrading: 0, Installing: 0, Removing: 0" (opencv/hailo packages already newest), unit
install/`daemon-reload`/`enable --now` produced no errors and **no "Created symlink" lines**
(already enabled), `systemctl show garuda-portal garuda-configd -p ActiveEnterTimestamp` was
**identical before and after** the re-run (`Thu 2026-07-30 14:13:34 BST` both services,
both runs) — proof the re-run converged without restarting either service. `bridge.agent_port`
config set from the E2E pass survived the re-run untouched. `hailortcli fw-control identify`
and `vcgencmd get_throttled` (`0x0`) sanity checks still passed at the tail of the re-run.

Deviations from the task brief: none. `python3-opencv` was already present on the bench
image before this deploy (pulled in earlier as a dependency of `rpicam-apps-opencv-postprocess`
/ tappas packages) — `install.sh` still declares it explicitly in its own apt list per the
brief, and apt correctly reports it "already the newest version" on both runs.
- Single 5-min window, bench idle; no load/thermal stress during capture.

---

## 2026-08-02 — garuda-mask: native Hailo YOLOv5n-seg dynamic-masking service (Task 1, hybrid ORB-SLAM3+YOLO plan)

Versions: kernel `6.18.34+rpt-rpi-2712`, `hailo_platform` 5.1.1 / py 3.13.5, Hailo `HAILO10H` FW 5.1.1,
OpenCV 4.10.0. Model: `/usr/share/hailo-models/yolov5n_seg_h10.hef` (+ `yolov5seg.json`), input
640×640×3 UINT8; outputs conv63 160×160×32 (proto) + 3 det heads 351ch. Probe input: `calib/captures/pair11_L.png`
(1280×800 rectified-left tile). Plan: `docs/superpowers/plans/2026-08-02-hybrid-orbslam3-yolo-masking.md`.

**Unit tests (hardware-free, on-bench, `python3 tests/test_mask.py`):** 5/5 PASS — dynamic-class
mask union/resize/dilate + latest-wins mmap IPC (`MaskIPC`).

**Decode validation (rule 2 — verified, not assumed):** on-CPU YOLOv5-seg decode returns real COCO
classes `['chair','person']` (not garbage), and geometry is correct — the `person` box center
(0.91, 0.32 of frame) matches its assembled mask centroid (0.94, 0.32). No coordinate bug. The probe
image is *adversarial* (a person holding the checkerboard, face/torso occluded): the detector fired
`person` on a background garment and missed the occluded centre human — i.e. the pipeline faithfully
masks what YOLO detects; it does **not** yet prove detection quality on a clean VIO scene.

**Timing (synchronous single-frame, mean of 15 after warmup):**

| Stage | Time | Rate |
|---|---|---|
| NPU infer only (`hailortcli`-equiv, blocking `run()`) | **83.8 ms** | 12 fps |
| CPU decode + NMS + mask assembly | **3.1 ms** | — |
| End-to-end (infer→dynamic mask) | **86.9 ms** | **11.5 fps** |

Exact objectness-gate optimization (gate cells on `obj > score_thr` before per-class sigmoid — loss-free
since `score = obj·cls ≤ obj`) cut end-to-end 125 → 87 ms with **identical** detections. Thermals during
runs: 47–49 °C, `get_throttled=0x0`.

**Red-team (rule 9):**
- The bottleneck is **NPU single-shot latency** (83.8 ms of the 86.9), *not* CPU postproc (3.1 ms). This is
  a 4-context seg model run **synchronously** (one blocking `run()` per frame) — it is NOT the streamed
  throughput number. P1's `yolov8m` hit 76 fps via `hailortcli benchmark` (pipelined). A pipelined/async
  masker should beat 11.5 fps; `TBD-bench`. 11.5 fps is the honest synchronous figure as built today.
- Detection quality on a real walking-person VIO clip is **unproven** (`TBD-bench`) — the adversarial probe
  only validates the decode/geometry/IPC path.
- Licence: `yolov5n-seg` is GPL-3.0 (Ultralytics). BENCH/EVAL only under the 2026-08-02 relaxation; masks
  consumed behind `IFrontEnd`, tagged `not-product-cleared`. Our masker code (`services/garuda-mask/`,
  `hosts/pi5/hailo_yolo_seg.py`) is original.
- The `garuda-depth` service holds the NPU exclusively (`HAILO_OUT_OF_PHYSICAL_DEVICES` if both run) — it was
  stopped for these runs and restarted after; the persistent masker will need depth stopped.

---

## 2026-08-02 — ORB-SLAM3 stereo builds + runs on Pi 5: EuRoC MH_01 go/no-go (Task 2, hybrid plan)

**The go/no-go gate for the whole hybrid.** ORB-SLAM3 stereo built in an aarch64 **Ubuntu-22.04 / GCC-11
container** on the Pi (dodging bare-metal GCC 14 — see decisions.md), headless (`bUseViewer=false`),
benchmarked on EuRoC **MH_01** (3682 stereo pairs, 752×480).

- Image: `garuda/orbslam3:bench` (3.29 GB). ORB-SLAM3 SHA `4452a3c4ab75`. Pangolin v0.6, OpenCV 4.5.4,
  Eigen 3.4 (all in-container). Host: Trixie, kernel 6.18.34, GCC 14.2 (NOT used for the build).
- Dockerfile/build.sh/run_euroc.sh: `hosts/pi5/orbslam3/`. stereo_euroc patched headless + to print
  per-frame tracking time (this ORB-SLAM3 rev collects `vTimesTrack` but never prints it, and wall-clock
  is meaningless — the example `usleep()`s to real-time 20 fps playback).

**Front-end tracking rate (the metric; per-frame `TrackStereo` time, playback-sleep excluded):**

| Measure | Value |
|---|---|
| median tracking time | **41.96 ms → 23.8 fps** |
| mean tracking time | **43.85 ms → 22.8 fps** |
| keyframes (MH_01) | 153 |
| wall clock (incl. vocab load + 20 fps playback sleeps) | 214.3 s (run 2), 237.9 s (run 1) |

**Verdict: PASS the P4 ≥20 Hz front-end bar** — ~22.8 fps tracking, achieved *even while thermally
throttled*. Better than the research forecast (single-digit-to-low-teens for Pi-class feature SLAM); the
Pi 5's 2.4 GHz A76 + modest 752×480 stereo carry it. ORB-SLAM3 stereo is viable on this bench → proceed to
the mask seam (Task 3).

**Red-team (rule 9):**
- **Thermally throttled.** Peak 84.5 °C (near the 85 °C hard limit); `get_throttled` ended `0xe0000`
  = arm-freq-capped + throttled + soft-temp-limit all *occurred* (bit 3 active at peak). **No under-voltage**
  (power OK). So 22.8 fps is a THROTTLED figure — unthrottled is higher, but sustained runs on the passive
  heatsink (CLAUDE.md §2) throttle, so ~22.8 fps is the realistic *sustained* rate. Clearing 20 Hz while
  throttled is the encouraging part.
- **Tracking-thread rate only.** The full P4 gate also wants ≥200 Hz pose + <20 ms latency — NOT met here:
  this is stereo-only (no IMU), so there's no high-rate inertial pose. Stereo-visual pose is frame-rate-bound.
- **EuRoC is ideal** (indoor, well-lit, 752×480, machine-hall). Live OV9281 stereo (different res, real
  conditions) is `TBD-bench`.
- **Accuracy — MEASURED (resolves the TBD).** SE(3)-aligned (Umeyama, no scale — stereo is metric) vs the
  EuRoC vicon groundtruth, 3638/3682 poses associated within 20 ms: **ATE RMSE = 4.66 cm** (mean 3.73 cm,
  median ~3.5 cm, max 15.0 cm) over an 80.5 m loop. Near the ORB-SLAM3 paper's *desktop* MH_01 figure
  (~3–4 cm) — so accuracy holds on the Pi. Note this trajectory run was HARD-throttled (warm start → 86.7 °C,
  ARM clock capped to 1500 MHz, `0xe0006`), i.e. accuracy survived even worst-case thermal conditions.
  Aligner `~/align_ate.py` on the bench; visualized at the trajectory-viewer artifact (2026-08-02).
  Still `TBD-bench`: live OV9281 accuracy (EuRoC is an ideal indoor set).
- Licence: ORB-SLAM3 GPL-3.0, BENCH/EVAL only, behind `IFrontEnd`, not-product-cleared (2026-08-02 relaxation).
- **⚠ MEASUREMENT CONTAMINATION (found post-hoc): `garuda-depth` was running at ~266 % CPU (2.6 cores)
  throughout every ORB-SLAM3 run above.** `camarray_depth_explorer.py` (restarted after Task 1) was doing
  continuous SGBM/point-cloud depth — so ORB-SLAM3's 3 threads shared only ~1.4 free cores of the Pi's 4,
  and the depth load added constant heat that worsened the throttling. **All figures above (22.8 fps, ATE
  4.66 cm, 86.7 °C) are therefore PESSIMISTIC** — clean (depth stopped, cooled) ORB-SLAM3 stereo will be
  faster and cooler. Verdict only strengthens: PASS was achieved CPU-starved + throttled. Depth stopped
  2026-08-02; a clean uncontaminated re-run is `TBD-bench`.

---

## 2026-08-02 — Task 3: clean-room dynamic-mask seam wired into ORB-SLAM3 (hybrid join proven)

The NPU masker (Task 1) → ORB-SLAM3 extractor (Task 2) join. Clean-room seam
(`hosts/pi5/orbslam3/patches/orbextractor_mask.py`) revives ORB-SLAM3's vestigial `_mask` param and
drops keypoints whose original-res position (`pt * scaleFactor[level]`) falls in a dynamic region,
before orientation/descriptors. Baked into the Dockerfile; verified with `feature_mask_demo`.

- **Proof (feature_mask_demo on `pair11_L`, fed the live Hailo `yolov5n-seg` dynamic mask):**
  `unmasked=1203` keypoints → `masked=1129` → **74 dropped** — exactly the keypoints inside the masked
  region (mask coverage 4.6 % + 12 px dilation). Before/after visualized (mask-seam artifact).
- End-to-end path confirmed: **Hailo YOLOv5-seg (NPU) → dilated dynamic mask → ORB extractor rejects
  keypoints there → clean features to tracking.** The NPU does masking only; CPU does the SLAM.
- **Red-team:** the demo image is adversarial — the masker tagged a background garment as "person"
  (not the checkerboard-occluded human), a Task-1 *detection* quirk. The *seam* is exact regardless (drops
  wherever mask>0). A full masked-vs-unmasked *trajectory/ATE* delta needs a real dynamic clip (moving
  person crossing a mapped scene) → `TBD-bench` (best captured live with the OV9281 pair, Task 4).
- Licence: ORB-SLAM3 GPL-3.0 + YOLOv5 GPL-3.0, BENCH/EVAL, behind `IFrontEnd`, not-product-cleared.

---

## 2026-08-03 — Live SLAM on OV9281 CamArray: root-caused to POOR STEREO APPEARANCE (not software)

Task 4 live attempt: recorded live stereo clips (selfie + room-facing, 87 & 275 frames), rectified via
proven `stereo_rig` maps, ran ORB-SLAM3. **It never tracks — only ~25 stereo map points per frame.**
Instrumented `Frame::ComputeStereoMatches` (counters on the match funnel) to pin it definitively:

```
N≈1206 left kp → rowOK≈1202 → maxU≈1202 → descPass≈40 → final≈20
```

- **Rectification is FINE:** rowOK≈1202 (right kp present on the same row band); best descriptor matches
  sit at **dy≈0** at every vertical search band. Not a calibration/vertical-alignment problem.
- **The collapse is descriptor/appearance matching:** only ~40/1200 keypoints pass the ORB Hamming
  threshold; **median best same-row Hamming ≈ 100/256 (near-random 128)**. Even a ±60-row search finds
  only 251/1500 good matches. SGBM density is only **28.9%** (vs 76% on earlier close high-contrast
  captures). Both descriptor AND intensity stereo matching fail → the two cameras produce poorly-
  correlated local image appearance.
- **Optics differ:** left image is **~20% sharper** than right (Laplacian-variance ratio 1.1–1.2 across
  frames) — a focus/lens mismatch. CLAHE/histogram-matching and wider vertical search do NOT recover it.

**Conclusion: the OV9281 CamArray is not a good stereo-SLAM pair** (geometric calib is fine, RMS 1.26px,
but photometric/appearance correspondence is too poor for dense stereo matching). This matches CLAUDE.md
§2: the OV9281 CamArray is earmarked "inference/tracking/VLM dev only"; the **IMX296 GS pair (sourcing) is
"the ONLY VIO-grade cameras."** Live stereo SLAM was always intended for the IMX296, not this quad.

**Software is proven & ready** (EuRoC 22.8 fps / ATE 4.66 cm; masker; seam; live tooling all work) —
re-run live on the IMX296 pair when it arrives. Possible interim: refocus the right lens and re-test.

**FOLLOW-UP (same day): refocus tested, does NOT fix it.** Built `hosts/pi5/camarray_focus.py` (live
sharpness meter). Owner manually focused both lenses — sharpness went **118/99 (mismatched) → ~600/~580
(both sharp AND matched)**, confirmed via `hosts/pi5/check_stereo_match.py`. **But stereo descriptor
matching was UNCHANGED: descPass ≈25/1200, median best-Hamming ≈101 (near-random).** Denoising (Gaussian
3/5, NLmeans) doesn't help either. So the OV9281 pair's poor correspondence is **NOT** focus, photometry,
noise, vertical alignment, L/R order, or config — it's fundamental to the hardware (mismatched cheap
optics/sensors). **Definitive: the OV9281 CamArray cannot do stereo SLAM; the IMX296 GS pair is the path.**
Silver lining: the better/matched focus helps the OV9281's actual jobs (depth/tracking/inference).

> **⚠️ 2026-08-03 — THIS "HARDWARE" CONCLUSION IS RETRACTED. See correction entry below. The OV9281
> stereo failure is a CALIBRATION + L/R-swap bug in our setup, NOT a sensor limit. The prior measurement
> only checked descriptor matching UNDER the (broken) rectification, so it read "random" — the assumed-good
> rectification was itself the bug.**

---

## 2026-08-03 — CORRECTION: OV9281 stereo failure is CALIBRATION, not hardware (root-cause redone)

Owner pushed back ("not convinced IMX improves tracking — must be something in the implementation"). Redid
the root-cause with controlled tests (scripts in scratchpad: `stereo_corr_battery.py`, `dydx_diag.py`,
`recover_pose.py`; copies on bench `~/`). Clean bench: `garuda-depth` off, no throttle. **The earlier
"near-random descriptors / hardware" verdict was WRONG.** Evidence:

- **Our pipeline is correct.** Identical stereo-correspondence funnel on EuRoC (control) = 765/1000 good
  matches, median Hamming 27, disparity +7 px — healthy. And OUR `rectify_clip.py` stored maps produce
  **bit-identical** results to an independent `cv2.stereoRectify` → our rectification code is not the bug.
- **The OV9281 pair IS valid stereo.** Free descriptor matching (BFMatcher crossCheck, no epipolar
  constraint) on OV9281 cam0↔cam1: **~900 matches, median Hamming 26** (≈ EuRoC's 23), and **80% fit a
  single fundamental matrix** (543/679 RANSAC inliers). Random/incompatible images cannot do that. The
  descriptors correspond and one rigid two-view geometry explains the pair.
- **The stored calibration mis-rectifies it.** With `camarray_stereo.yaml` (R,T), true correspondences land
  **113 px off their epipolar rows** (frac|dy|<2 = 0.00) at **negative disparity** — so ORB-SLAM3's same-row
  stereo search rejects ~all of them → "Fail to track." The earlier `check_stereo_match.py` measured
  Hamming *under this broken rectification* (comparing wrong points on the same row) → the misleading ~100
  "near-random" reading.
- **Correct geometry fixes it.** Recovering the true relative pose from the matches (essential matrix,
  542 inliers) and rectifying from THAT drops median |dy| **113 px → 1.3 px** (frac|dy|<2: 0.00 → 0.65).
  Recovered baseline dir/rotation differ from the stored calib (rot 7.5° vs ~1.3°), and disparity is
  negative → **the pair is also L/R-swapped** (cf. the existing `garuda_stereo_swap.yaml`/`room1_swap`).
- **Verdict:** OV9281 stereo is blocked by (a) a wrong/mismatched stereo calibration (`rms_stereo` was 1.26
  px — already a smell) and (b) L/R tile-order swap. **FIXABLE in software** — recalibrate the actual
  recorded pair with correct L/R order, verify |dy|≈0 after rectification, re-run ORB-SLAM3. **IMX296 is NOT
  required for stereo to work** (it remains the better VIO sensor, but the current wall is our setup).
- **END-TO-END CONFIRMED (same day):** rebuilt room1 with extrinsics recovered from the images (pooled
  essential matrix over the clip, 3894 inliers) + corrected L/R order (`~/build_corrected_clip.py` →
  `euroc_live/room1_fixed` + `cam.yaml`), then ran `stereo_euroc`. **Result: `Map 0 has 21 KFs`** — vs
  **1 KF** (never initialised) on every wrong-calib run. ORB-SLAM3 now initialises and maps the OV9281 pair.
  Tracking still drops on 43/87 frames ("Less than 15 matches") because the image-recovered rectification is
  loose (median |dy| 8.4 px, not sub-pixel; pooled essential-matrix E-inlier rate only 32%, hinting the two
  cams may not be perfectly rigid frame-to-frame → possible lack of hardware sync). 15.97 fps @1280×800
  (consistent with the pixel sweep). **This proves OV9281 stereo SLAM works once geometry is right (1→21
  KFs by fixing ONLY calib+swap).** Durable fix = proper checkerboard recalibration with correct L/R order
  and good coverage → sub-pixel rectification → continuous tracking (`TBD-bench`). If residual looseness is
  desync, that's the one place a hardware-synced pair (IMX296 external trigger) genuinely helps.
- **RECALIBRATION ATTEMPTED (same day) — existing checkerboard captures are UNUSABLE.** Found the captures
  (`calib/captures/pair*_{L,R}.png`, 80 pairs, 68 both-detected 9×6). Re-ran the project `stereo_calibrate.py`
  → reproduces stored calib exactly (mono RMS 1.17/1.26, stereo 1.26 px). BUT that calibration **does not
  rectify its own captures**: median checkerboard-corner |dy| after rectification = **126 px** (should be ~0),
  via both the stored R1/R2/P1/P2 and a fresh `stereoRectify(R,T)`. Low reported reproj-RMS + non-rectifying
  extrinsics = the pipeline converged to a self-consistent-but-wrong solution (its iterative outlier drop from
  68→51 pairs hid it). A clean-room `stereoCalibrate` on all 68 pairs (bypassing the script) gives **stereo
  RMS 29.7 px** and a garbage baseline (169 mm, huge Z) → **the L/R corner correspondences are fundamentally
  inconsistent** (mispaired captures, or L/R not simultaneous). Corner first-row-direction was consistent
  (0/68 flipped), so it's not a simple 180° flip. **Conclusion: recalibration from existing data is
  impossible — needs a fresh, well-diversified checkerboard shoot with verified-simultaneous L/R.**
- **BUT room1's own geometry is valid and rigid.** Per-frame essential-matrix recovery over room1 is stable
  (inter-cam rotation 7.9° ± 0.4°, low spread → NOT desync; the pair has a fixed real ~7.9° rotation the
  checkerboard calib wrongly reported as 1.7°). So **scene/self-calibration is the working path** (already
  proved: 21 KFs). Options forward: (a) bundle-adjust the room1-recovered extrinsics to sub-pixel → clean
  ORB-SLAM3 track without any checkerboard; (b) re-shoot a proper checkerboard calibration. Sensor is fine.

## 2026-08-04 — scene self-calibration of OV9281 room1 + the true ceiling (sync, not sensor/intrinsics)

Owner picked scene self-calibration. `~/self_calib.py` (scratchpad copy): pooled **21,941** stereo
correspondences over 43 room1 frames, estimated one essential matrix, refined R,t by numpy Gauss-Newton on
Sampson error, auto-fixed the L/R swap, rebuilt `euroc_live/room1_selfcal` + yaml. Clean run (garuda-depth
stopped, `0x0`). Result:

- Recovered inter-cam rotation **7.55°** (checkerboard calib wrongly said 1.7°), median stereo |dy| **3.0 px**
  (down from 113 px with the broken calib). **ORB-SLAM3: `Map 0 has 25 KFs`** (was 1 KF broken; 21 KFs with
  the loose image-recovered version), 28/87 frames drop, 1 map reset. 15.9 fps @1280×800. So: **maps and
  tracks intermittently — a big step from dead, but not yet clean continuous tracking.**
- **GN refinement did not reduce the residual** (Sampson RMS 3.3e-3 → 3.3e-3) → the extrinsics are already
  optimal; the ~3 px is NOT a pose error.
- **Ceiling attribution (perframe_vs_global.py):** per-frame OWN-pose rectification reaches **0–1 px** on good
  frames (frame 26: 0.0 px, frame 16: 1.0 px) → **the intrinsics ARE capable of sub-pixel rectification, so
  they are not the limiter.** But a single GLOBAL pose plateaus at ~3 px, and per-frame recovered rotation
  varies ±0.4° (≈ several px). Net: the wall on a single fixed calibration is **per-frame geometry variation**
  — most consistent with **mild desync** (OV9281 CamArray L/R not tightly triggered → during motion the pair
  is captured at slightly different instants, so effective stereo geometry drifts frame-to-frame). Caveat:
  own-pose partly overfits its own points; a **static-scene capture** would confirm desync definitively
  (if still ~3 px static → intrinsics; if it tightens → desync).
- **Corrected full picture:** (1) the dominant failure was a grossly BROKEN calibration (extrinsics + swap) —
  fixing it took ORB-SLAM3 from 1 → 25 KFs; owner's "it's the implementation" was right. (2) Descriptors and
  intrinsics are fine (sub-pixel achievable). (3) The remaining ceiling to CLEAN continuous tracking is
  capture **synchronization** — and THAT is the specific, legitimate reason a hardware-triggered GS pair
  (IMX296 + XTR trigger) is needed for production VIO. NOT "cheap optics / random descriptors" (that verdict
  was wrong). Durable path: synced IMX296 pair + a proper checkerboard calibration → EuRoC-class (<1 px) →
  clean tracking. `TBD-bench`: static-scene desync confirmation; IMX296 live when it arrives.

> **⚠️ 2026-08-04 LATER — THE "DESYNC CONFIRMED" CONCLUSION BELOW IS RETRACTED.** A follow-up motion-scaling
> test (`~/motion_corr.py`) refutes desync: stereo |dy| does NOT scale with camera motion speed
> (corr r = −0.03), the slowest-third frames are actually WORSE (8.2 px) than the fastest-third (3.3 px), and
> the fit intercept is 5.1 px — nowhere near the static 0.24 px floor. Desync predicts the opposite (dy ∝ speed,
> intercept ≈ static floor). So the static-vs-one-moving-clip comparison was NOT valid proof of desync; the
> static 0.24 px is a narrow single-viewpoint case (self-cal pose fit+evaluated on ~identical frames), and
> room1's ~3–8 px misfit is viewpoint/calibration-dependent, NOT motion-induced. **We CANNOT conclude a
> sensor/sync issue. Most likely: no good stereo calibration exists yet (checkerboard set broken; scene
> self-cal from room1 too ill-conditioned to pin the fixed rig geometry). Next: a proper fresh checkerboard
> calibration is required before any sensor/sync claim.** Owner flagged the over-reach; correct call.

### 2026-08-04 — static-scene desync test: CONFIRMED (the OV9281 ceiling is sync, not the sensor) [RETRACTED — see note above]

Captured 25 stereo pairs from the STATIONARY OV9281 (`~/static_capture.py` → `euroc_live/static1`,
garuda-depth stopped) and compared per-frame geometry stability + rectified |dy| vs the moving room1 clip
(`~/static_analysis.py`, both scratchpad copies):

| clip | scene motion | inter-cam rotation | rectified \|dy\| (single global calib) |
|---|---|---|---|
| **static** | 7.8 gray/frame | **2.36° ± 0.33°** | **0.24 px** |
| moving (room1) | 11.2 gray/frame | 7.73° ± 0.70° | 2.99 px |

- **On a static scene the OV9281 pair rectifies to 0.24 px — sub-pixel, EuRoC-class.** The ~3 px wall appears
  ONLY under motion. **Definitive: the tracking ceiling is motion-induced desync** (L/R captured at slightly
  different instants; during motion that injects ego-parallax the fixed rectification can't track).
- **Tell:** the recovered "inter-camera rotation" itself inflates 2.36° (static) → 7.73° (moving) — that extra
  ~5° is ego-rotation between the two unsynchronized grabs. So room1's self-cal 7.55° was motion-contaminated;
  the true rig rotation is ~2.4°. (Also why the checkerboard calib's ~1.7° was in the right ballpark yet
  still non-rectifying — its captures had their own pairing/quality problems.)
- **FINAL, COMPLETE picture of the whole OV9281-stereo saga:** (1) the sensor/optics are excellent — static
  rectification 0.24 px, descriptors match at Hamming ~26; the original "cheap optics / random descriptors /
  hardware wall" verdict was fully WRONG and is retracted. (2) The calibration was genuinely broken (extrinsics
  + swap) → fixing it took ORB-SLAM3 1 → 25 KFs. (3) The one remaining, real limit is **capture
  synchronization** — proven by the static test. **This is the exact, specific engineering reason the product
  needs a hardware-triggered global-shutter pair (IMX296 + 1.8 V XTR trigger): not image quality, but
  simultaneity.** For static/slow scenes the OV9281 is already sub-pixel. Software desync compensation is
  possible in principle (know δt + IMU) but hardware sync is the clean fix.
- **P4/VIO implication:** the IMX296 GS pair remains the product path — now for the correct reason (sync), and
  with confidence it will work (a synced, well-calibrated pair rectifies like the static 0.24 px → EuRoC-class
  → clean tracking). OV9281 CamArray = fine for its assigned jobs (detection/tracking/depth/VLM) and for
  static/slow stereo, not for dynamic stereo VIO.

### 2026-08-04 — DEFINITIVE (controlled still-then-move clip): calibration was the blocker; OV9281 tracks clean

Owner flagged both prior over-swings ("desync confirmed" and "desync refuted") and asked for a controlled
motion test. Captured ONE clip, camera still then moved, same scene (`euroc_live/move1`, 207 pairs @5 fps,
owner did the motion). Classified every frame by measured inter-frame motion; fit stereo geometry from the
STILL frames only (desync-clean); measured rectification |dy| per motion bin (`~/move_analysis.py`); rebuilt
the clip with that still-fit calibration and ran ORB-SLAM3 (`~/build_move.py`). Scratchpad copies of both.
**This supersedes the room1 numbers and BOTH 2026-08-04 desync claims above.**

- **Still-fit rig geometry 2.41°** (matches the 2.36° static test; NOT room1's motion-contaminated 7.55°).
- **|dy| vs motion (same scene, same calib):** still 0.00 px → slow 1.20 → med 1.58 → fast(23.6px/frame)
  **1.86 px**; `dy ≈ 0.108·motion`, r=0.61, intercept ≈ 0. **Real desync exists (~10–16 ms L/R offset) but is
  SMALL — ≤~2 px even at fast handheld motion, inside ORB-SLAM3's scale-dependent stereo band.**
- **ORB-SLAM3 on the still-fit clip: 2/207 fail-to-track (~1 %), 17 KFs — clean continuous tracking** (2 early
  resets = the 88 opening still frames, no parallax). vs room1's motion-contaminated calib: 28/87 (~32 %).
  **The calibration was the blocker, full stop.**
- **FINAL verified conclusion (retracts every earlier verdict):** (1) OV9281 sensor/optics fine — descriptors
  match, still rectification 0.00 px. (2) The whole failure was a **broken/contaminated stereo calibration**
  (checkerboard set unusable; self-cal MUST be fit from STILL frames — moving frames poison the geometry).
  Correct calib → **OV9281 does dynamic stereo SLAM at ~1 % frame loss.** (3) Small real desync (~10–16 ms,
  ≤2 px) is a **precision refinement**, not a blocker; IMX296 + XTR trigger removes it for best accuracy but
  is NOT required for working tracking. **Owner's original instinct — "implementation, not the sensor; IMX296
  isn't the fix" — was correct.** Prior "hardware wall / random descriptors" AND my "must-have sync" verdicts
  are both wrong/retracted.
- **Method note (future OV9281 self-cal):** fit extrinsics from STILL frames only — fitting from a moving clip
  inflates recovered inter-cam rotation by ego-motion (2.4° → 7.5°) and wrecks rectification.
- `TBD-bench`: ATE on a self-cal OV9281 run (needs groundtruth); IMX296 live (now expected to just work).

- Rule-9 note: this is exactly the failure rule 9 guards against — an assumed-correct component (rectification)
  was the bug, and measuring "through" it produced a confident-but-wrong "hardware" verdict.

---

## 2026-08-03 — ORB-SLAM3 stereo, 2nd public dataset: TUM-VI room1 (fisheye) + cross-dataset viewer

Second public-dataset validation of the same `garuda/orbslam3:bench` stereo binary, on a **harder** set:
TUM-VI **room1** (`dataset-room1_512_16`, 2821 stereo pairs, **512×512 fisheye — KannalaBrandt8**, handheld,
**no IMU used** — pure stereo). Runner `~/run_tumvi.sh` on the bench stops `garuda-depth` first, so this is a
**depth-stopped run** (unlike the contaminated EuRoC runs above). Model `stereo_tum_vi` patched headless
(`System::STEREO,false`); already prints median/mean track time. Config path `Examples/Stereo/TUM-VI.yaml`.

- **Config bug fixed (upstream ORB-SLAM3):** shipped `Examples/Stereo/TUM-VI.yaml` writes
  `Camera.overlappingBegin/End` where the settings loader requires `Camera1.overlappingBegin/End`
  (the Stereo-Inertial variant has it right) → `stereo_tum_vi` aborted with "required parameter does not
  exist". Fixed the prefix (`s/^Camera.overlapping/Camera1.overlapping/`) and committed into the bench image.

**Front-end tracking rate** (per-frame `TrackStereo`, playback-sleep excluded):

| metric | value |
|---|---|
| median tracking time | **52.42 ms → 19.1 fps** |
| mean tracking time | **52.37 ms → 19.1 fps** |
| keyframes | 178 KFs, 1 map (no track loss — all 2821 frames tracked) |

Slower than EuRoC's 22.8 fps as expected: fisheye (KannalaBrandt8) unprojection is costlier than pinhole.
**Still clears the P4 ≥20 Hz bar? — NO, 19.1 < 20 Hz on this handheld fisheye set.** EuRoC (22.8) clears it;
TUM-VI fisheye sits just under. Honest read: front-end rate is camera-model- and scene-dependent; the GS
IMX296 pair (global-shutter pinhole, the actual product camera) is closer to the EuRoC regime than the
fisheye. Recorded as a data point, not a gate pass/fail.

- **Accuracy — MEASURED.** SE(3)-aligned (Umeyama, no scale) vs TUM-VI **mocap0** groundtruth,
  2773/2821 poses associated within 20 ms: **ATE RMSE = 7.79 cm** (mean 7.59 cm, max 14.01 cm) over a
  **146.4 m** handheld trajectory. `datasets/euroc_ate.py` used unchanged (TUM-VI mocap0 = same ns +
  first-4-column layout as EuRoC GT; est/GT timestamps both in ns and aligned — verified before trusting).
- **Red-team (rule 9):** 7.79 cm is *pure-stereo, no-IMU* on fisheye. The ORB-SLAM3 paper's room1 numbers
  are **stereo-INERTIAL** (~cm) — NOT comparable; IMU tightly constrains scale/rotation that pure stereo
  must recover visually. So 7.79 cm is a fair pure-stereo figure, not a regression vs the paper. Larger
  than EuRoC's 4.66 cm because: (a) handheld = faster/shakier motion than the EuRoC MAV, (b) fisheye
  periphery is lower-resolution per solid-angle, (c) no inertial aid. Thermals/`get_throttled` **not
  captured for this run** → not claimed.
- **Cross-dataset viewer built** (user request "make a way to view the data set and the slam output on a
  page"): interactive 3D orbit trajectory (est coloured by per-pose error vs dashed groundtruth), readout
  panel (ATE/fps/KFs/pathlen), and cam0 sample frames — EuRoC MH_01 + TUM-VI room1, dataset toggle.
  Self-contained HTML Artifact; all figures pulled from measured runs. Builder scripts in scratchpad
  (`make_traj_json.py`, frame sampler) — trajectory JSON is regenerable from `f_*.txt` + GT via
  `datasets/euroc_ate.py`'s alignment.
- Licence unchanged: ORB-SLAM3 GPL-3.0, BENCH/EVAL only, behind `IFrontEnd`, not-product-cleared.

---

## 2026-08-03 — pixel-density → ORB-SLAM3 front-end cost sweep (OV9281, our camera)

Answering "how much do our extra pixels cost?" Public sets are LOW-res on purpose (EuRoC 0.36 MP,
TUM-VI 0.26 MP); our OV9281 is 1.02 MP/tile (2.8×/3.9× their pixels), IMX296 1.58 MP (4.4×/6.0×). Ran the
same rectified OV9281 clip (`euroc_live/room1_rect`, 87 pairs, 1280×800) through the bench `stereo_euroc` at
3 scales, **`ORBextractor.nFeatures` fixed at 1200** to isolate the per-pixel extraction/pyramid cost from
the fixed descriptor/match cost. Runner `~/ov_sweep.sh` (scales images + intrinsics, keeps physical
baseline). **Clean run: `garuda-depth` off, 58.7 → 62.6 °C, `get_throttled=0x0` throughout — no
contamination, no throttle.**

| scale | resolution | MP | median track ms | front-end fps |
|---|---|---|---|---|
| 1.00 | 1280×800 | 1.024 | **60.88** | **16.2** |
| 0.75 | 960×600 | 0.576 | 39.91 | 24.2 |
| 0.50 | 640×400 | 0.256 | 26.87 | 35.7 |

- **4× the pixels (0.256 → 1.024 MP) = 2.27× the front-end time** (35.7 → 16.2 fps). Sub-linear because a
  fixed ~15 ms/frame (1200-feature descriptors + stereo-match attempts + per-frame overhead) doesn't scale
  with pixels; only the pyramid+FAST extraction does.
- **Linear fit (this Pi, A76, nFeatures=1200, no BA):** `front-end median ms ≈ 15.1 + 44.4 × MP`
  (fixed 15 ms + 44 ms/MP). Predicts OV9281-full 16.5 fps (measured 16.2 ✓), and **IMX296-full 1.58 MP →
  ~11.7 fps** — i.e. running the product camera at native res would sit well under 20 Hz. **Argues to
  downscale IMX296 toward the ~0.3–0.5 MP EuRoC/TUM-VI regime** (keep global shutter + sync, shed pixels).
- **⚠ These fps are extraction+match ONLY — tracking never converges** ("Fail to track local map!" on all 87
  frames × every scale; `Map 0 has 1 KF`), so local-map/BA cost is absent. On a sequence that *tracks* add
  BA overhead → real fps lower (cf. EuRoC 0.36 MP tracked at 22.8 fps *with* BA vs the model's ~32 fps
  front-end-only at that pixel count; the gap ≈ the BA cost). So the model is a front-end floor on cost.
- **Bonus finding (reconfirms `dafdb70`):** tracking fails identically at 640×400, 960×600, 1280×800 —
  **downscaling does NOT rescue the OV9281's near-random stereo descriptor matching. Resolution-independent
  → hardware, as root-caused. IMX296 GS pair remains the only stereo-SLAM path.**
- Scripts: `~/ov_sweep.sh` on bench (scratchpad copy `ov_sweep.sh`); yaml `hosts/pi5/orbslam3/garuda_rect.yaml`.

## 2026-08-04 — S1 garuda-track: live detect + track + click-to-lock on the CM3 (perception core)

`services/garuda-track`: CM3 IMX708 → Hailo YOLOv8m (reuses garuda-inference's GStreamer pipeline) →
vendored ByteTrack (MIT) → lock FSM → `TrackedObjects` mmap IPC, plus a click-to-lock overlay (:8092 MJPEG).
Built subagent-driven (Tasks 1–5, pure logic, each spec+quality reviewed) + controller-led bench integration
(Tasks 6–8). Plan/spec: `docs/superpowers/{plans,specs}/2026-08-04-garuda-track-*`.

Measured on this Pi, garuda-depth + portal stopped (single-tenant camera/NPU), headless (`--view-port 0`):

| metric | value |
|---|---|
| front-end fps (capture→detect→track) | **median 29.6** (samples 28.3–29.8) |
| process CPU (1 core) | median 69 %, peak 70 % |
| SoC temp peak | 51.6 °C |
| `vcgencmd get_throttled` | **0x0** (no throttle) |

- 29.6 fps is the camera→detect→track rate; the NPU running yolov8m alone is 76 fps (P1), so the front end is
  bound by capture + GStreamer + tracking, not the NPU. Real Hailo labels (person/chair/backpack) via
  `get_label()`. With the :8092 overlay+click view on, ~29.5 fps (overlay/JPEG cost negligible).
- **Click-to-lock validated live:** clicking a detected target → `LOCKED(id)`; unlock → `SEARCHING`. The
  `TrackedObjects` IPC is readable by a separate consumer (the seam S2/distance + S4/follow will plug into).
- Single-tenant camera/NPU: `hosts/pi5/run_track.sh` stops garuda-depth + portal first (idempotent).
- Licence-clean: ByteTrack (MIT, vendored), OpenCV (Apache-2.0), Hailo (LGPL-dyn) — no GPL.
- Harness: `hosts/pi5/track_bench.py`. Scope note: this is S1 (detection+track+lock); distance (S2), obstacle
  sectors (S3), follow/avoid control (S4) are separate sub-projects, not measured here.

## 2026-08-04 — Depth-panel CPU spike diagnosed + fixed (opt-in panel + depth fps cap)

Symptom (user): "the moment I lock a target CPU shoots to 90%"; "even the NPU-based distance
measurement spikes CPU to 90%; object tracking alone when locked is 30%." Root-caused live (top -bn1,
ss, /api/current) — NOT the lock. Two independent causes:

1. **Auto-loaded SGBM depth panel.** The portal was auto-embedding the garuda-depth `/stream` (:8097)
   in the tracking view. garuda-depth computes only while a viewer is connected; that one browser
   viewer pinned **SGBM at ~190% CPU (2 cores)** on top of detection. Verified: exactly 1 ESTAB
   connection to :8097 (the browser) ↔ garuda-depth at 190%.
2. **No fps cap on the depth loop.** Every engine — NPU included — rectifies + colorizes + JPEG-encodes
   a full depth map per frame, all CPU, and the loop ran flat-out. The NPU only shortcuts `infer()`.

Measured on this Pi (1 :8097 viewer + CM3 tracking running):

| state | garuda-depth CPU | system load |
|---|---|---|
| SGBM (CPU), flat-out | ~190 % (2 cores) | ~5.8 |
| garuda-inference (tracking) alone, locked | ~110 % (1 core ≈ 30 % of 4) | — |
| **fast_depth (NPU) @ 6 fps cap** | **40 % (0.4 core)** | **1.79** |

- fast_depth NPU infer capability is **126 fps** (meter); a distance readout needs a few fps, so the
  uncapped rectify/colorize/encode wrapper was the hog. Cap → 0.4 core.
- SGBM is genuinely compute-bound at ~6 fps end-to-end (rectify+disparity+colorize+encode of a 640×400
  map from a 5120-wide GREY capture), so the fps cap is a no-op for it — SGBM stays ~2 cores. Use an NPU
  engine, not SGBM, for live depth alongside tracking.
- Red-team: `vcgencmd get_throttled` read **0xe0000** after the overload — sticky bits 17-19
  (freq-cap / throttle / soft-temp-limit *have occurred*), low bits 0x0 (not now), bit 16 clear (no
  under-voltage ever). So the passively-cooled Pi *did* throttle under the ~5.8 overload — the very
  condition removed here. Post-fix idle 65.9 °C.

Fixes shipped (deployed to bench + repo):
- `services/garuda-portal/static/{index.html,app.js,style.css}`: depth panel is **opt-in, off by
  default** (Off / SGBM·CPU / StereoNet·NPU / fast_depth·NPU buttons). Tracking/locking alone no longer
  starts any depth compute; leaving inference forces the panel off so no viewer leaks.
- `hosts/pi5/camarray_depth_explorer.py`: `DEPTH_FPS_CAP` (default 6, `GARUDA_DEPTH_FPS` env) throttles
  the whole compute+encode chain.

Not yet solved (needs P0): per-*target* distance. garuda-depth reads the OV9281 stereo pair and reports
center-of-frame distance; it is not calibrated to the CM3 that does the tracking, so it cannot yet give
the tracked object's distance. Needs the CM3↔OV9281 extrinsic calibration on a rigid mount (deferred).

## 2026-08-04 — S2 stereo target-range (Option B): metric distance of the locked target

Feature: detect + single-target lock + range entirely in `garuda-depth` on the OV9281 stereo pair
(yolov8m on the rectified LEFT → best instance of the locked class → bbox-ROI disparity → Z=f·B/d),
surfaced in the portal "Target Range" tab (click-to-lock + badged metric distance). Plan/spec:
`docs/superpowers/{plans,specs}/2026-08-04-stereo-target-range*`. NPU decision: `docs/decisions.md`.

NPU findings (Task-1 spike, this Pi, HailoRT 5.1.1):
- yolov8m_h10 solo **36.2 fps**; NMS output parses to a per-class list.
- **Cross-process NPU sharing FAILS** (`HAILO_OUT_OF_PHYSICAL_DEVICES`); the multi-process service
  daemon is not packaged → chose Option B (single process). **Intra-process** sharing works:
  fast_depth (NPU depth) **115 fps** + range concurrent on the shared vdevice, no wedge.

Measured (`hosts/pi5/range_bench.py`, office chair at an UNMEASURED distance — precision only):

| metric | value |
|---|---|
| hit rate (valid readings) @ conf 0.3 | **48/48 = 100 %** |
| distance median / min / max | 1.648 / 1.426 / 1.734 m |
| distance std (precision) | **0.103 m** (~6 % at 1.65 m) |
| garuda-depth CPU (range active) | **~0.70 core** (idle when cleared) |
| SoC temp | 60.9 °C |
| `vcgencmd get_throttled` | 0xe0000 — sticky freq/throttle/soft-temp bits from an earlier overload; low bits **0x0** (not throttling now), bit 16 clear (no under-voltage) |

- **Accuracy vs ground truth = TBD-bench** — needs a target at a tape-measured distance (the run below).
- Grayscale OV9281 lowers yolov8m confidence (a chair sat ~0.40–0.53) → conf floor set to 0.3 for a
  steady lock; a 1.5 s UI hold smooths residual flicker.
- Red-team (rule 9): the 0.103 m spread is dominated by the chair's large, soft bbox (ROI disparity
  varies as the box breathes) and by there being **2** overlapping chair detections; a compact,
  well-separated target should tighten it. The distance is not yet ground-truthed — do NOT treat the
  ~1.65 m as validated until the tape-measure run lands here.

## 2026-08-07 — YOLO26-depth metric monocular depth on Hailo-10H (garuda-mono)

- Built `yolo26n-depth.hef` for hailo10h on the x86 bench PC (Ultralytics 8.4.115 `format=hailo`,
  DFC 5.3.0 from AI SW Suite 2026-04; compile 13m48s CPU-only). First YOLO26-depth HEF on this bench.
- Pi self-test (`hailo_yolo26_depth.py`, CM3 still frame 1280x720): output 192x192 metric map,
  **30 ms/inference**; room scene read min 2.19 m / med 2.62 m / max 3.37 m — plausible scale,
  NOT ground-truthed (tape-measure run = TBD-bench).
- garuda-mono with YOLO26 detect (every frame) + YOLO26-depth (every 3rd frame):
  **14.5 fps distinct processed frames over 12 s** = camera-limited (CAM_FPS=15) — the loop keeps up.
  CPU ~4% total, temp 63.7 °C, no throttle.
- Caveats (rule 9): HEF calibrated on only 4 auto-downloaded images at DFC optimization_level=1
  (QAT finetune + level-2 algos OOM-killed a 14 GB x86 box; disabled — see decisions.md).
  Quantization accuracy is therefore a lower bound; production build needs ~1024 representative
  calib images and a bigger-RAM/GPU box. Metric accuracy vs tape = TBD-bench.

## 2026-08-07 (evening) — yolo26m-depth PRODUCTION HEF (1024-img calib, level-2 PTQ) — probe gate PASS

- Built `yolo26m-depth.hef` (31.9 MB, 768x768 in, 192x192 out) on the x86 PC with the GPU restored
  (driver 580.173.02): DFC optimization_level=2, a16 outputs, 1024 calibration images (256 live CM3
  frames + 768 SUN RGB-D val). QAT finetune DISABLED — GPU OOM at batch 8/4/2 on the 6 GB RTX 3060
  (decisions.md); PTQ-only. Layer Noise Analysis stats skipped (GPU OOM, warning only). Wall time
  ~40 min end-to-end, ~26 min of it allocator/compile (log timestamps 17:04 LNA -> 17:33 HEF).
- Probe gate (collapsed-HEF check), on-Pi standalone run on a live CM3 bench frame (cm3_0150.jpg):
  depth min 0.96 / med 1.28 / max 2.80 m, **logit std 0.263** vs the ~0.04 collapse signature — PASS.
- Deployed: HEF -> Pi `~/garuda-sdk-deploy/models/yolo26m-depth_hailo_model/`, `Y26D_HEF` switched,
  drop-in `GARUDA_MONO_DEPTH=y26d`, cal reset, service active.
- Red-team (rule 9): the probe frame comes from the same static bench scene as 256 of the 1024
  calib images — the spread could flatter the calib distribution. Move-test on unseen viewpoints
  + tape-measure accuracy run required before trusting the metric scale. Both = TBD-bench.

## 2026-08-07 (late) — yolo_world_v2s open-vocab detector on Hailo-10H (garuda-mono world mode)

Task 7 of the portal-tracking plan: prompt-driven ("open-vocabulary") detection as a live
alternative to the COCO-80 YOLO26 head, behind `POST /api/detector`. All figures below are
**measured on the bench Pi** (garuda@10.42.0.128, HailoRT/fw 5.3.0, kernel 6.18.34, Python 3.13.5).
Wrapper: `hosts/pi5/hailo_yolo_world.py`. Model card (contract + sources): `models/yolo_world_v2s_h10.md`.

**Spike's two open questions — both CLOSED, measured, not assumed:**

| question | measured answer |
|---|---|
| Is the `(1,80,512)` UINT16 embedding input quantized, and with what scale? | Yes. `qp_scale=2.6535412e-05`, `qp_zp=9778.0`, limvals `-0.25946325 .. 0.61002260` (read from `HEF.get_input_vstream_infos()`; the CLI has no flag for this). `(0.61002260+0.25946325)/2.6535412e-05 = 32767` — exact. |
| Can we sidestep hand-packing uint16? | Yes. `infer_model.input("…/input_layer2").set_format_type(FormatType.FLOAT32)` is accepted; configure + infer succeed and HailoRT quantizes. This is the path shipped. |
| Are our CLIP embeddings the tensor the DFC calibrated on? | Our L2-normalised `text_embeds` for "pen, keys, person" span **-0.261 .. 0.598** vs the HEF's calibrated **-0.259 .. 0.610**. |

**Latency (standalone, NPU otherwise idle, 640x640 letterboxed, batch 1):**

| stage | measured |
|---|---|
| `yolo_world_v2s` infer, warm | **~36 ms** (34–37 ms over 6 single-frame timings) = **~28 fps** equivalent |
| CLIP ViT-B/32 text tower, Pi CPU (onnxruntime, 2 threads), cold session + 3–5 prompts | **0.83–1.14 s**, once per new prompt word (cached thereafter) |

Zoo publishes 46.6 fps for this HEF at batch 1 — we measure ~28 fps single-shot from Python with
per-frame bindings; the gap is not investigated (no async/batched path tried).

**Correctness — same HEF, same image (`/tmp/bus.jpg`), vs the YOLO26m COCO head as control:**

| detector / slot padding | bus | person (best 3) |
|---|---|---|
| YOLO26m, COCO-80 fixed head (control) | 0.93 | 0.94 / 0.93 / 0.93 |
| yolo_world_v2s, prompts + **zero**-filled slots | **0.922** | **0.898 / 0.898 / 0.898** |
| yolo_world_v2s, prompts + `' '` background-embedding padding | 0.819 | 0.477 / 0.399 / 0.399 |

Boxes agree with the control to within a few px (e.g. bus 14,227,792,519 vs 11,228,787,506).
Zero padding is therefore the shipped default — see the model card for why.

**Open-vocabulary actually works** (`CONF=0.10`, prompts that are NOT COCO classes):
`pavement 0.395 (6,646,802,434)` and `bus wheel 0.187 (686,559,77,120)` on `/tmp/bus.jpg`.

**Live-camera sanity:** the bench CM3 was pointed at a blank wall all session; YOLO26m at conf 0.15
and yolo_world at conf 0.05 both returned **0 detections** on the live frame (`/tmp/probe.jpg`) —
consistent, and the reason "pen, keys, person" produced nothing live.

### Integrated `/api/detector` bench — MEASURED (2nd attempt, after the power event below)

Re-run on the Pi as a detached, fsync-after-every-line script (`/tmp/world_bench.sh` →
`~/world_bench.log`) so a repeat power loss would leave the exact reproduction point on disk.
Fresh boot, `garuda-portal` stopped so mono owned the CM3; live bench scene (a person, a suitcase,
a chair, curtains) — not the blank wall of the earlier attempt. **It completed; nothing crashed.**

| step | measured |
|---|---|
| `mode=bogus` | `{"ok": false, "reason": "mode must be primary|world"}`, detector untouched |
| `mode=world` with no prompt | `{"ok": false, "reason": "world mode needs at least one prompt"}`, detector untouched |
| **PRIMARY** loop, 12 s | **12.89 fps** distinct processed frames; SSE **10.22 Hz**; 3 tracks (chair, person, suitcase); 52.7 °C |
| **switch → world** ("pen, keys, person"), cold | `{"ok": true, …}` in **4.50 s** wall — lazy HEF configure + ORT session load + CLIP encode + smoke infer, all inside the POST |
| **WORLD** loop, 12 s | **14.79 fps**; SSE **10.23 Hz**; 1 track (person); 54.3 °C |
| **re-prompt** while in world mode ("person, chair") | `{"ok": true, …}` in **0.76 s** — HEF already configured; "person" cached, "chair" newly encoded |
| **switch → primary** | `{"ok": true, "mode": "primary", "prompt": []}`, snapshot confirms `{'mode': 'primary', 'prompt': []}` |

World mode runs **faster** than primary here (14.79 vs 12.89 fps): yolo_world_v2s is the smaller
network and its NMS is on-chip, whereas YOLO26m's 6 raw heads are decoded in numpy on the CPU.
Both sit at/near the CAM_FPS=15 ceiling, so this is close to camera-limited either way.

`vcgencmd get_throttled` **`0x50000` unchanged across every step** (before the switch, immediately
after the switch, and after the 12 s world load), 51.6 → 54.3 °C.

### Confirmation run — repeated on a later, clean-supply boot (`0x0` at start)

The Pi was power-cycled again (PSU possibly swapped) and came up reading `throttled=0x0`. The whole
sequence was re-run from that clean state, this time also capturing the journal and a live
open-vocab prompt. Deployed files were md5-verified identical to commit `e59916e` first.

| step | run 1 (`0x50000` at start) | run 2 (`0x0` at start) |
|---|---|---|
| PRIMARY loop, 12 s | 12.89 fps / SSE 10.22 Hz / 3 tracks | **12.99 fps** / SSE 10.21 Hz / 5 tracks |
| cold switch → world | 4.50 s | **4.53 s** |
| WORLD loop, 12 s | 14.79 fps / SSE 10.23 Hz | **14.96 fps** / SSE 10.23 Hz |
| re-prompt (HEF already configured) | 0.76 s (2 words, 1 new) | **0.85 s** (5 words, all new) |
| both negative cases rejected, detector untouched | yes | yes |
| back to primary, confirmed in snapshot | yes | yes |
| SoC temp | 51.6 → 54.3 °C | 53.2 → **57.6** °C |

Reproducible to within ~1 %. **`garuda-mono` journal for run 2 is clean** — systemd start/stop lines
plus two known-benign warnings only: HailoRT's `Cannot create log file hailort.log` (cwd not
writable, pre-existing) and onnxruntime's `Failed to detect devices under /sys/class/drm/card*`
(GPU probe on a headless Pi, emitted once when the CLIP session opens).

**Live open-vocab on the real bench scene — partial.** Prompts
`curtain, human face, suitcase, ring light, hair` at conf 0.10 produced exactly one track:
`suitcase 0.576 (330,274,227,423)`, spatially matching the blue suitcase that YOLO26m also boxes.
The four non-COCO prompts did **not** fire on this scene. So the load-bearing open-vocab evidence
remains the `bus.jpg` run above (`pavement` 0.395, `bus wheel` 0.187); this live run confirms the
integrated path prompts and localises correctly, but is **not** additional evidence that arbitrary
non-COCO prompts work — a fair reading is that these particular prompts on this particular scene
scored below 0.10.

### The under-voltage reproduced on a SECOND, independent boot — and again not from world mode

Run 2 started at `throttled=0x0` and read **`0x50000` at 16:09:31** — that is, **during the PRIMARY
baseline 12 s load test, before the world switch had been issued** — then stayed at `0x50000`
unchanged through the switch and the world load. Run 1 showed the same pattern from the other
direction (already `0x50000` before the switch).

Two independent boots, two UV strikes, both triggered by the **ordinary** mono workload
(CM3 capture + YOLO26m every frame + depth every 3rd), neither by the open-vocab path. Combined with
the 2026-08-02 TUM-VI entry above ("third UV strike for this supply", identical `0x50000`), the
conclusion is that **this bench's 5 V rail sags under sustained CPU+NPU load regardless of which
detector is running**, and that the replacement supply — if one was fitted — has not fixed it.
CLAUDE.md §2's "verify 5 V/5 A" is now the highest-value open action on this machine: every perf
number recorded here, this entry included, is taken on a rail that browns out.

### Power: `0x50000` was ALREADY set before the world switch — not caused by this feature

The first attempt at this bench ended with the Pi going completely dark mid-switch (host NIC
`NO-CARRIER` = unpowered PHY, i.e. a power event, not a kernel soft-hang), off for ~30 min until it
was physically power-cycled. Post-mortem was **inconclusive from software**: `journalctl` on this
Pi is volatile (`no persistent journal was found`), so the previous boot's kernel log is gone, and
the reboot cleared the sticky bits.

What the re-run does establish: on the **fresh** boot, `get_throttled` read **`0x50000`** at 4
minutes uptime — **bit 16 (under-voltage has occurred)** and bit 18 (arm frequency capped has
occurred) both set, low bits `0x0` (not throttling at read time) — and that was measured **before**
the world switch ran, during nothing more exotic than a service restart + camera + the primary
detect/depth loop. The world switch then ran with the flags **unchanged**. So the under-voltage is a
property of the bench supply, not of the open-vocab path.

This is also not new: the 2026-08-02 TUM-VI entry above records the identical `0x50000` on this
machine and calls it "third UV strike for this supply". CLAUDE.md §2's "verify 5 V/5 A" is still the
open action, and per that rule perf numbers from this bench stay suspect until it is resolved.

**Recommended (system change — needs owner go-ahead, rule 10): make the journal persistent**
(`sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald`). Had it been on, the
first crash would have been diagnosable instead of guesswork.

### Not verified

- Tracking **quality** in world mode over a moving target: untested. World scores run lower than
  YOLO26's and ByteTracker's `high_thr` is 0.4 (`WORLD_CONF` defaults to 0.10 so the low-confidence
  association stage still sees them), but no move-test was run.

Red-team (rule 9): (a) the score comparison uses one image (`bus.jpg`) — it shows the pipeline is
wired right, it is NOT an mAP claim; the zoo's own number is float 36.4 → hardware 31.9 mAP. (b) the
fps figures are camera-limited at 15 fps, so they measure "the loop keeps up", not detector
throughput; the standalone ~36 ms (idle NPU) is the honest per-inference number. (c) world mode held
only 1 track vs primary's 3 on the same scene, but with a different 3-word vocabulary — that is not
a recall comparison and must not be read as one. (d) all of the above was measured on a supply that
tripped under-voltage during the runs themselves; rule-1 numbers stand as recorded, but distrust
them per CLAUDE.md §2. (e) the live open-vocab run returned only the one COCO-word prompt, so it
does not by itself demonstrate open-vocabulary — `bus.jpg` does.

## 2026-08-07 (night) — Task 8: yolo26m 960² detect HEF — GATE FAIL (fps, not power)

Task 8 of the portal-tracking plan: build yolo26m at `imgsz=960` (vs the shipped 640) to test
whether the extra resolution helps far/small-object recall, gated behind a new
`GARUDA_MONO_YOLO` systemd-drop-in override (`hosts/pi5/garuda_mono.py`). All figures below
**measured on the bench Pi** (garuda@10.42.0.128, HailoRT/fw 5.3.0).

**Build (x86 PC, `/media/bhanu1234/Development/hailo-export`, RTX 3060, driver 580.173.02,
DFC 5.3.0, exporter level-2/finetune-disabled — unchanged from the depth build):**

```
cd work && TMPDIR=../tmp ../venv/bin/yolo export model=yolo26m.pt format=hailo name=hailo10h \
  imgsz=960 data=/media/bhanu1234/Development/hailo-export/datasets/depth-calib.yaml
```

`depth-calib.yaml` (the depth build's calib set: 256 CM3 + 768 SUN RGB-D, single-class "depth"
yaml with no detect labels) was accepted by the detect-task loader as **1024 label-less
backgrounds** ("0 images, 1024 backgrounds, 0 corrupt") — exactly the fallback path the brief
anticipated, and it worked, so **no fallback to `coco128.yaml` was needed**. `yolo26m.pt`
auto-downloaded (42.2 MB). Wall time **1645.9 s (~27.4 min)** end-to-end (Partition+Allocation
20m43s + Kernel Compilation 18s + remaining HEF build); output `yolo26m_hailo_model/yolo26m.hef`,
**18.0 MB**, md5 `9fb1d50b5c263921ad36510348dd0496` — verified identical after `scp -r` to the Pi
at `~/garuda-sdk-deploy/models/yolo26m960_hailo_model/`.

### Blocker found and fixed: the detector wrapper hardcoded the 640 input size

Deploying the HEF and flipping `GARUDA_MONO_YOLO` produced an immediate, 100%-reproducible
failure on every frame:

```
[HailoRT] [error] CHECK failed - Input buffer size 1228800 is different than expected 2764800
                   for input 'yolo26m/input_layer1'
mono infer error: Invalid operation. See hailort.log for more information
```

`1228800 = 640*640*3`, `2764800 = 960*960*3`. `hosts/pi5/hailo_yolo26.py` had a module-level
`IN_SIZE = 640` baked into both the letterbox-resize in `HailoYolo26.infer()` and the default
in `decode_yolo26()` — it never asked the HEF what size it actually wanted. This is not something
the brief's file list anticipated (it named only `garuda_mono.py` + `benchlog.md`), but without
fixing it **no swapped-in HEF at any resolution other than 640 could ever run** — the whole point
of the `GARUDA_MONO_YOLO` override — so it was in-scope by necessity, not scope creep.

**Fix (same file, same commit):** `HailoYolo26.__init__` now reads
`self.in_h, self.in_w = tuple(self.im.input(self.iname).shape)[:2]` off the configured model —
the exact pattern already used by the sibling wrappers `hailo_yolo26_depth.py` and
`hailo_yolo_world.py`, which never had this bug. `infer()` letterboxes to `self.in_w/self.in_h`
instead of the constant; `decode_yolo26()` gained `in_w=IN_SIZE, in_h=IN_SIZE` parameters (default
640 preserved for any caller that doesn't pass them, none currently exist) instead of closing
over the module constant. `PAIRS`' hardcoded conv-node names (`conv71/74/87/90/101/104`) were
**not** touched — same yolo26m architecture regardless of `imgsz`, and they resolved correctly
against the 960 HEF's output names with no changes. x86 unit tests: **31 passed, 1 skipped**
(hosts/pi5, no regression). Redeployed (md5-verified); the 960 HEF then ran clean — `age_ms`
populating, zero HailoRT errors in the journal after the fix.

### Pre-existing CM3 arbitration bug reproduced (not fixed, out of scope)

At gate start `garuda-portal`'s own `rpicam-vid` (started at boot) held the CM3, starving
`garuda-mono`'s capture thread (its own `rpicam-vid` child was `<defunct>`). This is the same
single-consumer race flagged in the Task 7 report ("whoever starts first wins"). Stopped
`garuda-portal` for the gate (Task 7 precedent), restarted `garuda-mono` so it took the camera,
ran the gate, then **restarted `garuda-portal` afterward** to restore the pre-task arrangement —
this time `garuda-mono` won the race back (it was already running), so portal's own `rpicam-vid`
ended up `<defunct>` post-gate. Both services report `active` regardless; this is a latent bug in
the deployed topology, unrelated to this task, worth its own fix.

### Gate measurement

Stream viewer: `curl -s -o /dev/null http://127.0.0.1:8098/stream &` (backgrounded on the Pi, per
the brief — "counts as one"). Method: poll `GET /api/state` at 20 Hz for 60 s; each sample's
`age_ms` backs out the wall-clock time of the last *processed* frame
(`poll_time − age_ms/1000`); consecutive samples whose derived frame-time differ by ≤3 ms are
the same frame (deduped); `processed_fps = distinct_frame_count / elapsed_s`. Harness:
`/tmp/gate_bench.py` on the Pi (not committed — throwaway harness, mirrors the `/api/events`
freshness approach the brief specifies).

| metric | measured |
|---|---|
| duration | 60.01 s |
| poll samples / errors | 1198 / 0 |
| distinct processed frames | 441 |
| **processed fps** | **7.35** |
| `vcgencmd get_throttled`, start | `0x0` |
| `vcgencmd get_throttled`, end | `0x0` |
| SoC temp, start → end | 73.6 → 74.1 °C |
| scene | blank bench wall throughout, 0 tracks (consistent with every prior session on this rig) |

**Far-object qualitative check: not assessable on current scene.** The CM3 points at a
featureless bench wall (frame saved, not committed: `person: searching...` overlay, no texture or
edges anywhere in view) — there is no small/far object in frame to compare 960-vs-640 recall on,
and staging one was not possible from this session. Not faked.

### GATE VERDICT: FAIL

Threshold is **≥15 fps camera-limited with depth co-resident AND low throttle bits 0x0**.
Throttle was clean (**0x0 at both ends — this is NOT a power-caveat failure**, unlike several
earlier entries in this log), but **7.35 fps is well under half the 15 fps floor**. Per Step 3 of
the brief: removed the `yolo960.conf` drop-in, `daemon-reload` + restarted `garuda-mono` (back to
the stock 640 `yolo26m.hef`, confirmed via `systemctl show -p Environment` — only
`GARUDA_MONO_DEPTH=y26d` remains), restarted `garuda-portal`. **The 960 HEF stays on the Pi**
at `~/garuda-sdk-deploy/models/yolo26m960_hailo_model/` for future use, per the brief.

**Confirmation run (2nd boot).** Immediately after the revert above, the Pi dropped off the
network entirely (`ping`: 100% loss, then `Destination Host Unreachable` from the gateway —
harder than the usual ssh-accept stall) and came back on its own ~16 minutes later with
`uptime` reading **up 0 min** — a genuine, uncommanded reboot, consistent with this bench's
well-documented power flakiness (see the Task 7 entries above) rather than anything this task
did. Post-reboot state matched exactly what had been left: only `depth.conf` present, env only
`GARUDA_MONO_DEPTH=y26d`, `garuda_mono.py`/`hailo_yolo26.py` md5s unchanged, `throttled=0x0`,
`yolo26m960_hailo_model/` still on disk — nothing was lost or needed re-fixing. With the Pi
healthy again, the gate was **re-run once more** (same drop-in, same method, `garuda-portal`
stopped/restarted around it exactly as before) to address red-team point (a) below:

| | run 1 | run 2 (2nd boot) |
|---|---|---|
| distinct processed frames / 60 s | 441 | 444 |
| **processed fps** | **7.35** | **7.40** |
| `vcgencmd get_throttled`, start → end | 0x0 → 0x0 | 0x0 → 0x0 |
| SoC temp, start → end | 73.6 → 74.1 °C | 65.9 → 67.0 °C |

Reproducible to ~1%, throttle clean both times — **the FAIL verdict is confirmed, twice, and is
definitively not a power artifact.** Reverted a second time (drop-in removed, `garuda-mono`
restarted to stock 640, `garuda-portal` restarted) — final state: both services `active`,
`throttled=0x0`, journal clean.

Red-team (rule 9): (a) ~~single 60 s run, not repeated on a second boot~~ — **addressed**: a
second independent-boot run reproduced the fps to ~1% (§ confirmation run above), so this is now
double-verified the way this log's other entries are. (b) the scene had **zero** detections
the whole run, so the Python-side NMS/decode loop (which only does real work on grid cells above
`conf_thresh`) was doing close to nothing per frame — 7.35 fps is therefore a number for the
*letterbox-resize + NPU-inference* cost, not a stress case for the decode loop; a busier scene
could plausibly measure slightly lower, not higher. (c) `y26d` depth (768² input) still runs every
3rd frame co-resident by design (per the brief, "depth co-resident" is part of the gate) — the
7.35 fps conflates detect-at-960 cost with depth cost and NPU contention between them; no
detect-only isolation run was taken to attribute the shortfall between "960² inference itself is
~2.25x the pixel/compute area of 640²" vs "NPU contention with the depth engine" vs "host-side
letterbox resize at 960² costing more per frame in Python/OpenCV" — any follow-up should profile
per-stage before trying a middle ground (e.g. `imgsz=768`). (d) the wrapper fix
(`hailo_yolo26.py`) is a real behavioural change beyond the brief's stated one-line scope; it was
verified by unit tests (no regression) and by the 960 HEF now running error-free, but **no
box-correctness check was run at 960** (no `bus.jpg`-style score/box comparison against the 640
control, unlike Task 7's detector-swap verification) because the live scene had nothing to detect
— this fix is verified to *run*, not verified to be *equally accurate* at 960 vs 640. (e) the CM3
single-consumer race was worked around, not fixed — a future task should arbitrate the camera
between `garuda-mono` and the V1 portal properly.

## 2026-08-07 (close) — Task 9: portal-tracking close-out — camera-lazy fix + validation sweep

Task 9 of the portal-tracking plan (final task): close-out docs plus a controller-approved scope
addition — the portal's own `CameraStream` was grabbing the CM3 unconditionally at service start,
racing `garuda-mono`'s rpicam-vid for the one CSI camera ("whoever starts first wins", flagged in
Tasks 6/7/8 and hit twice during those tasks' bench work). Fixed and bench-verified below. All
numbers in this entry are freshly measured on the bench Pi (garuda@10.42.0.128) this session,
except where explicitly citing Task 7/8's already-recorded figures (carried forward unchanged,
per rule 1 — no new numbers invented for those).

### A. Camera-lazy fix — implementation + bench evidence

`services/garuda-portal/camera_stream.py`: `CameraStream.start()` made idempotent (no-op if the
reader thread is already alive); new `acquire()`/`release()` ref-count viewers and start/stop the
reader at the 0→1 / 1→0 transitions — mirrors the `_viewers` pattern already in
`hosts/pi5/garuda_mono.py` and `hosts/pi5/camarray_depth_explorer.py`. `services/garuda-portal/portal.py`:
new `stream_handler_for(inference, camera_stream)` acquires the camera only when the connection is
serving the raw stream (not the `InferenceManager` inference preview) and releases it in a
`finally` on disconnect; `main()` no longer calls `stream.start()` at boot. `static/index.html` +
`static/app.js`: the Camera tab's `<img>` no longer has a static `src="/api/stream"` (which used to
open the connection the instant the page loaded, any tab) — it now sets/clears `src` on
tab-enter/tab-leave, the same lazy-load pattern already used for the Depth/Range/Tracking tabs.
Also removed the Tracking panel's vestigial Start/Stop/model-select controls
(`static/index.html` + `app.js`) — they commanded `InferenceManager`'s CM3-child path, retired by
this branch's convergence (the Tracking tab now reads mono's stream directly); `InferenceManager`
and `/api/actions/inference` are left in place as an unreached debug-only backend capability, per
the spec's "the :8098 GUI survives as a debug page" framing — not deleted, just no longer wired to
any UI control.

Unit tests (x86, mock spawn/stream — same pattern as the existing `FakeStream`/`FakeManager`
fakes in `test_inference_manager.py`): 6 new — `CameraStream` idempotent-start, acquire/release
ref-counting (single + concurrent viewers), release-without-acquire no-op; `stream_handler_for`
acquire-on-connect/release-on-disconnect, leaves the camera untouched when serving the inference
preview, ref-counts two concurrent viewers. `services/garuda-portal/tests`: **81 passed, 1
skipped** (was 75p/1s). `hosts/pi5`: **35 passed, 1 skipped**, unchanged. Commit `03eb56c`.

**Deployed to the Pi** (`sudo cp` into `/opt/garuda/garuda-portal`, all 4 files md5-verified
identical to the worktree before restart):

```
$ sudo systemctl restart garuda-portal
Aug 07 17:42:59 raspberrypi systemd[1]: garuda-portal.service: Killing process 849 (rpicam-vid) with signal SIGKILL.
Aug 07 17:42:59 raspberrypi systemd[1]: garuda-portal.service: Deactivated successfully.
Aug 07 17:42:59 raspberrypi systemd[1]: Started garuda-portal.service - Garuda portal (web configurator).
$ ps aux | grep rpicam-vid
garuda  21505  rpicam-vid -t 0 -n --camera 0 --codec mjpeg --width 1280 --height 720 --framerate 15 --inline -o -
```

**The regression this fix exists to prevent, verified:** before the deploy, `garuda-mono`'s
`/api/state` read `"age_ms": null` (blind — the old portal code had won the boot race for the CM3,
its `rpicam-vid` running since boot, and mono's own capture child was `<defunct>`). After the
restart, only **one** `rpicam-vid` process is running on the box (the line above — mono's, by its
exact argv), and mono's `/api/state.age_ms` sampled fresh over four consecutive polls with the
portal up and idle (no camera-tab viewer): **12 ms, 72 ms, 80 ms, 41 ms** (all ≪ mono's own 1500 ms
staleness threshold). `vcgencmd get_throttled` = `0x0` throughout. This is the direct evidence
mono keeps the camera across a portal restart now, where it previously lost it.

**Partial verification, interrupted by the bench link:** a live check of the *other* half of the
contract — that opening the portal's own `/api/stream` (Camera tab) briefly re-acquires the CM3
and releases it again on disconnect — was starting (`curl` against `:8080/api/stream`) when the Pi
dropped off the network entirely and did not come back within this session's remaining window (see
§C). That specific live confirmation is **not done**; the logic it would confirm IS covered by the
6 new x86 unit tests above (acquire-on-connect/release-on-disconnect against a fake CameraStream),
so it is unit-verified but not bench-verified end-to-end. Flagged, not hidden.

### B. Task 7/8 measured numbers — carried forward unchanged (rule 1: no new numbers here)

Full detail in the "2026-08-07 (late)" and "2026-08-07 (night)" entries above; headline figures
only, for the close-out record:

| | measured | caveat |
|---|---|---|
| world-mode open-vocab, PRIMARY loop | 12.89 / **12.99** fps (2 boots) | run 1 under logged `throttled=0x50000` (UV has-occurred); run 2 clean (`0x0`→ started clean, unclear if it strayed mid-run — see original entry) |
| world-mode open-vocab, WORLD loop | 14.79 / **14.96** fps (2 boots) | same caveat |
| world switch (cold) | 4.50 / 4.53 s | — |
| yolo_world_v2s standalone infer | ~36 ms (idle NPU) | not the integrated number |
| 960² yolo26m detect gate | **7.35 / 7.40 fps** (2 boots) vs ≥15 fps floor → **FAIL** | throttle clean `0x0`→`0x0` both times — genuinely compute-bound, NOT a power artifact |

### C. Bench validation sweep (Task 9, Step 1 + dispatch sweep list)

Attempted via curl (no browser) against `garuda-mono` (:8098) and `garuda-portal` (:8080).
**The Pi went hard-down mid-sweep** (link drop, no ssh, no ping — consistent with this bench's
documented power-flake pattern, see Tasks 7/8 above) and had not returned by the end of this
session (background retry loop ran ~8 minutes with no success). Per the dispatch's tolerance rule,
everything x86-side was finished regardless; unswept items are listed honestly, not guessed at.

| sweep item | result |
|---|---|
| `garuda-mono` `/api/state` v1 snapshot sane | **DONE** — `v:1`, well-formed, `age_ms` fresh (see §A) |
| Camera-lazy regression (mono keeps CM3 across a portal restart) | **DONE** — see §A |
| portal `/` loads | **not swept** — Pi went down first |
| portal `/api/events` emits `mono`+`detections`+`follow` kinds | **not swept** |
| `POST /api/depth_engine` both directions + snapshot reflects | **not swept** |
| `POST /api/detector` world w/ prompt then back to primary | **not swept** |
| `POST /api/mono_calibrate_reset` ok | **not swept** |
| cal add (requires a locked target) | **not swept** — would likely have been "not assessable" anyway (no confirmed live target in scene this session) |
| portal Camera-tab lazy acquire-on-view (live) | **not swept** — interrupted mid-attempt, see §A; unit-tested, not bench-confirmed |
| Interactive click-through (browser) | **TBD for the owner** — out of scope for a curl-only sweep regardless of link state |

**DONE_WITH_CONCERNS.** The camera-lazy fix (this task's main deliverable) is implemented, unit
tested (81p/1s + 35p/1s), deployed, and its core regression is bench-verified. The rest of the
Step-1 validation sweep (portal routes, mono engine/detector switches, cal reset) is **not
completed this session** — the Pi was reachable for ~20 minutes at the start (enough to deploy and
verify the regression fix) and then dropped for the remainder. These items need a follow-up sweep
next time the bench is up; they are not blocked on any code change, only on link stability.

### D. Red-team (rule 9) — what could flatter these results

1. **Task 7's world-mode fps numbers were taken partly under a logged under-voltage condition**
   (`throttled=0x50000`, bit 16 "has occurred") — CLAUDE.md §2 says explicitly to distrust perf
   numbers when that flag is set. Run 1's PRIMARY/WORLD fps are under that flag for the whole
   window; run 2 started clean but the entry doesn't re-confirm `0x0` at every intermediate sample,
   only start/end-of-step reads. Until the power path is fixed (§ CLAUDE.md §7 NEXT), these numbers
   should be read as "what this flaky rail produced", not a clean ceiling.
2. **Every live-camera measurement this branch (Tasks 7, 8, and this task's own age_ms samples)
   was taken against a blank bench wall or a static, uncontested scene.** Zero detections most of
   the time means the Python-side decode/NMS/tracking loop was doing minimal work; a busier scene
   with multiple real tracks could plausibly run slower on all of these paths, not faster. The one
   partial exception (T7 run 2's "person, chair, suitcase" scene) is noted as such in its own
   entry, not generalized here.
3. **Link flaps coincide with, and possibly are caused by, the same power draw the fps/thermal
   numbers are trying to characterize** — this session's own hard-down happened right after a
   deploy+restart+camera-hardware exercise, similar to the drops recorded during Tasks 7 and 8.
   If the drops are downstream of the same under-voltage condition, then "the numbers we could
   collect" and "the sessions that didn't get cut short" may be a survivorship-biased sample of the
   bench's actual behavior — the runs that would show the *worst* thermal/power numbers are exactly
   the ones most likely to end in an unreachable Pi before they can be recorded.
4. **Single-operator verification throughout** — no second person or second machine cross-checked
   any of Tasks 7, 8, or this task's readings; every number in this branch has one source.
5. **This task's own regression check (age_ms freshness) is a strong, direct, mechanistic
   demonstration** (only one rpicam-vid process running, matching mono's exact argv, and mono's own
   staleness threshold cleared by 30-100x) — it is the one figure in this entry NOT subject to (1)
   or (2) above, since it doesn't depend on scene content or sustained load, only on which process
   holds the camera device.

---

## 2026-08-10 — Power supply upgrade: official Pi 27 W (5 V⎓5 A) installed + rail verified

Owner installed the official Raspberry Pi 27 W USB-C PD supply, replacing the 60 W GaN charger
whose 5 V leg negotiated only 3 A (measured root cause of the 2026-08-09 brownouts; see that
day's entries + decisions.md). Clean shutdown → swap → fresh boot. Measured on this boot
(garuda@10.42.0.128, HailoRT/fw/pcie 5.3.0 stack unchanged from versions.md 2026-08-07):

- Negotiated supply contract: `/proc/device-tree/chosen/power/max_current` = **5000 mA**
  (was **3000 mA** on the GaN charger, read 2026-08-09).
- `vcgencmd get_throttled` = **0x0** at fresh boot AND re-read ~20 s later with `garuda-mono`
  active (YOLO26m + depth + camera hot). No under-voltage bit, no throttle bits.
- `vcgencmd pmic_read_adc EXT5V_V` = **5.16838 V** with mono active (was 4.955 V at *idle* on
  the old charger).
- `vcgencmd measure_temp` = 62.6 °C with mono active (passive heatsink, ambient uncontrolled).

Consequences: the standing "keep mono stopped except short windows" constraint is lifted (its
root cause is fixed). Task-7-era numbers taken under `0x50000` (red-team item 1 above) are now
re-runnable on a clean rail. Sustained-load thermal behavior on the new supply: TBD-bench
(passive heatsink throttling at ~84 °C is a separate, still-real ceiling — power ≠ thermals).

---

## 2026-08-10 — Pose+gesture Task 5: fps gate, live gesture sessions, threshold freeze

Setup: `garuda-mono` pose mode on the demo stand (CM3, 1280×720@15fps config), zoo
`yolov8m_pose_h10.hef` (640²), depth co-resident, MJPEG viewer attached during gates, 27 W PSU
(rail verified this morning, this log). Method: processed-frame rate = `/api/events` SSE data-line
count over 60 s (same method as the Task 7/8 entries). Stack: HailoRT/fw/pcie 5.3.0 (versions.md
2026-08-07 snapshot still current).

### A. fps gate (plan floor: ≥15 fps, throttle low bits 0x0)

| config | events/60 s | fps | get_throttled | temp |
|---|---|---|---|---|
| pose, yolov8m_pose | 598 | **9.96** | 0x0 → 0x0 | 56.5→59.8 °C |
| pose, yolov8s_pose (plan fallback) | 599 | **9.97** | 0x0 → 0x0 | 68.1→69.2 °C |
| control: primary, no pose | 599 | **9.98** | 0x0 → 0x0 | 68.6→72.5 °C |

Verdict: the literal ≥15 floor FAILS in ALL configs including no-pose — **pose adds no measurable
fps cost** (Δ ≤ 0.02 fps across a 3× model-size change and removal). Camera is configured for
15 fps (`CAM_W, CAM_H, CAM_FPS = 1280, 720, 15`); the ~10 fps ceiling is the shared per-frame
pipeline (depth co-residency + host loop), pre-existing this branch. The s-variant HEF is retained
on the Pi (`yolov8s_pose_h10.hef`) but the m-variant is the deployed default (no speed benefit
from s; m has the larger capacity). Shared-pipeline optimization = separate workstream, out of
scope for this branch.

### B. Live gesture sessions (owner at the demo stand, CM3 frames — closes the card's
"real-camera validation" pending item)

- **2 m** (subject box ~350×445 px): all four gestures fired with correct debounce — point_left
  1105 ms hold; stop 1382 ms; point_right 1379 / 1025 ms; follow_me 1302 / 1085 ms. **Zero false
  activations** across ~10 min of watched windows including idle standing, walking, sitting.
- **4 m** (box ~120×280 px): follow_me fired 2× (1196 / 1582 ms — the second surviving a ByteTrack
  id flip 54→73 mid-hold). stop/point attempts were silenced by the original all-five-keypoint
  confidence gate: idle-wrist conf 0.06–0.19 and nose 0.04–0.64 while the gesturing wrist held
  0.82–0.91. → per-rule confidence gating implemented (owner-ruled, commit 26addd4), 17/17 unit
  tests. **Post-change live 4 m verify: TBD-bench** (owner ended the session; one post-change
  point_left did fire live at 1135 ms).
- **Close range** (<~1.5 m, upper-body framing): idle wrists sit at the bottom frame edge
  (y≈680–700 of 720) at conf ≤0.2 — gestures structurally unreachable. **Operating envelope:
  full upper body (head→hips) must be in frame.** Recorded in the model card.
- Track identity at 4 m churns heavily (5 ids in 70 s) — classifier unaffected (largest-subject
  selection); the portal's (name, track_id) onset dedup re-fires re-lock on id change by design.

### C. Frozen constants (Task 5 freeze, commit 26addd4)

`CONF_FLOOR 0.35, V_ALIGN_FRAC 0.18, H_BAND_FRAC 0.12, H_EXT_FRAC 0.30` + per-rule gating
(documented edge: both-arms STOP with one unconfident wrist reads as the confident arm —
follow_me if vertical, else silent).

### D. Red-team (rule 9)

1. The fps method (SSE cadence) matches prior entries but the 9.96–9.98 clustering across wildly
   different compute loads says the ceiling is a pacing artifact of the shared loop — these
   numbers characterize the pipeline, NOT the pose models' capability. Do not quote "10 fps" as a
   pose cost.
2. Temps climbed monotonically across back-to-back gates (56→72 °C, passive heatsink) without
   reaching the ~84 °C throttle plateau — longer sustained runs than 60 s were not measured.
3. Gesture results: single operator, single lighting environment, one camera angle. The 2 m
   "zero false positives" claim covers ~10 min of observation, not days.
4. The per-rule gating change is unit-tested but its live 4 m improvement is unverified
   (TBD-bench) — the single post-change point_left is anecdotal, not a gate.

**2026-08-10 addendum (multi-person demo scene):** apparent "gestures not detected" with 6
people in frame traced to designed lock-subject semantics — a stale lock (created by earlier
FOLLOW ME tests) sat on a non-operator track; non-locked STOP is ignored by design. After
self-re-lock: STOP held 1.4 s / 3.6 s / 6.95 s continuously, the last surviving a lock id churn
(356→373) mid-hold. Operator rule recorded in the model card.
