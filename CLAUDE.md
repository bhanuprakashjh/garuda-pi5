# CLAUDE.md — Garuda SDK Bench (Pi 5 + Hailo-10H)

Project context for Claude Code. Read fully before acting. This file is the source of truth for what this machine is, what we are building, and the working rules. If anything here conflicts with an instruction in-session, ask.

---

## 1. What this project is

**Garuda SDK** — Microchip's companion-computer autonomy stack for drones/robotics. This machine is the **bench/dev tier**: Raspberry Pi 5 + Hailo-10H, developing host-agnostic services that later port to a Hailo-15H SoM (product tier). The flight controller is a **separate** device (Microchip PIC32CZ CA90 running PX4) connected over Ethernet — it is NOT part of this machine and hard real-time control NEVER runs here.

Full plan: see `docs/garuda-sdk-plan.md` and `docs/garuda-sdk.pdf` in this repo (copy them in if missing). Key architecture rules:

- Inner control loop never crosses Ethernet. We publish odometry + obstacles; FC owns attitude/rate.
- Failsafe gates on odometry covariance/validity, never on link liveness.
- One PTP clock domain for everything: camera triggers, image timestamps, IMU (from FC over DDS).
- Every sensor sits behind a plugin interface (`ICaptureBackend`, `IFrontEnd`, `IDepthSource`) — no Pi-specific calls outside `hosts/pi5/`.
- Licence policy is HARD: BSD/MIT/Apache/LGPL-dynamic only in services. **No GPLv3 code or dependencies** (no OpenVINS, VINS-Fusion, ORB-SLAM3, no GPL snippets from forums). linuxptp as external daemon is fine. If unsure about a licence, stop and flag. **[RELAXED 2026-08-02, owner directive: GPLv3/AGPL are permitted for BENCH / EXPERIMENTAL / EVALUATION work (running locally ≠ distribution). Keep any GPL component behind the plugin interfaces (ICaptureBackend/IFrontEnd/IDepthSource) and tag it `not-product-cleared`; never deep-wire GPL into shippable `services/`. Product-tier licence-clean remains the goal — revisit before Phase 8. See docs/decisions.md.]**

## 2. Hardware actually present (update this section as it changes)

| Item | State |
|---|---|
| Raspberry Pi 5, 8 GB | present, boots. **Cooling is a PASSIVE heatsink (no fan)** — OS registers no fan/cooling_device; plateaus ~84 °C and thermally throttles (`get_throttled` bit 3) under sustained one-core CV/NPU load. Keep on-device tools light (cap fps, throttle loops). Corrected 2026-08-01 (was wrongly "Active Cooler"); see decisions.md |
| AI HAT+ 2 (Hailo-10H, 40 TOPS, 8 GB on-module) | mounted via 30 mm PCIe FFC + 16 mm stacking header |
| 256 GB microSD | boot + OS |
| Power | USB-C — **verify 5 V/5 A**; run `vcgencmd get_throttled` after load tests; 0x0 = OK. If under-voltage flagged, note it in the bench log and distrust perf numbers |
| Camera Module 3 (IMX708, rolling shutter) | attached & verified 2026-07-29 on CAM/DISP 0, first light captured — inference/tracking/VLM dev only. CSI is NOT hot-pluggable: attach only with the Pi fully powered off. Cable orientation: gold contacts toward Ethernet at Pi end, toward PCB at camera end |
| 2× GS Camera (IMX296, global shutter) | sourcing — the ONLY VIO-grade cameras. XTR trigger is **1.8 V logic — 3.3 V destroys the sensor**; R11 rework required for external trigger |
| Arducam T2 ToF | planned — depth plugin, mapper/landing workstream only |
| USB H.264 camera | planned — recording path (Pi 5 has NO hardware video encoder; never software-encode during benchmarks) |
| LAN966x TSN switch EVB, CA90 FC board | not yet on bench — PTP work is point-to-point or against scope until then |

**No flight hardware on this bench. Nothing here arms motors. If code touches MAVLink arming or offboard setpoints, it must be gated behind `GARUDA_SIM=1` or an explicit bench flag.**

## 3. This machine's software environment

- OS: Raspberry Pi OS **Trixie 64-bit** (Debian 13). Switched from Bookworm 2026-07-29: the Hailo-10H `h10-*` packages exist only in the Trixie repo — Bookworm (now oldstable) carries only the Hailo-8 line; see `docs/decisions.md`. OS major upgrades are always a **fresh flash, never in-place** (unsupported on Raspberry Pi OS). Record `uname -a` and package versions in `bench/versions.md` after every upgrade — Hailo driver/kernel coupling is the #1 bring-up failure.
- Hailo stack: install with `sudo apt install hailo-h10-all`. **Package family is `h10-*` / `h10-hailort` — NEVER mix with plain `hailort`/`hailo-all` (that's the Hailo-8 line).** If both appear installed, purge both, reinstall h10 only.
- Sanity: `hailortcli fw-control identify` must report Hailo-10H. `lspci | grep -i hailo` must show the device. Device in lspci but not hailortcli = version mismatch; absent = reseat FFC.
- Known Hailo pitfalls (apply ONLY on symptom, not pre-emptively):
  - vDMA timeout (−110): add `pcie_aspm=off` to /boot/firmware/cmdline.txt; `kernel=kernel8.img` in config.txt; `options hailo1x_pci force_desc_page_size=4096` in /etc/modprobe.d/hailo_pci.conf
  - PCIe Gen3 instability: fall back to Gen2 (`dtparam=pciex1_gen=2`) and note it — 10H holds weights in on-module DRAM so Gen2 is acceptable
- Cameras: libcamera/rpicam stack. `dtoverlay=imx296,cam0` etc. when GS cams arrive; `trigger_mode=1` via v4l2 for external trigger.
- ROS 2: **decided 2026-07-29 — Jazzy in Docker on Pi OS** (LTS to 2029; PX4 uXRCE-DDS compatible; see `docs/decisions.md`). Keep ROS thin — native on Pi OS: garuda-camera/-time, HailoRT inference, Basalt (plain C++, wrapped thinly); in the container: ROS nodes (VIO wrapper, mapper, planner), px4_msgs, rviz/rosbag2. DDS across the boundary: `--network=host --ipc=host`, one RMW pinned in both worlds (CycloneDDS). Container images build only from a committed Dockerfile in `hosts/pi5/` — never pulled-and-modified interactively (same idempotency bar as install.sh). Before Phase 4, services are plain C++/Python with the pipe/topic layer stubbed.
- Time: linuxptp (`ptp4l`, `phc2sys`); RP1 MAC has hardware timestamping (`ethtool -T eth0` to confirm).
- Python: system 3.13 under Trixie; Hailo python bindings come from the h10 packages — use venvs with `--system-site-packages` so bindings remain visible. If pyHailoRT misbehaves, verify the binding was built for the running Python (3.13) **before** debugging anything else — HailoRT/Python-version coupling is a known community pain point.
- Builds: prefer cross-compile from x86 CI for big targets; on-device builds use `-j2` (8 GB RAM) with zram/swap enabled.

## 4. Repo layout (create if missing, keep to this)

```
garuda-sdk/
├── CLAUDE.md                  # this file
├── docs/                      # plan md/pdf, bring-up guides, decisions log
├── services/                  # garuda-camera, -vio, -inference, -bridge, -time, -health, -logger, -portal, -ota
├── interfaces/                # ICaptureBackend, IFrontEnd, IDepthSource, message schemas — THE contract
├── hosts/pi5/                 # overlays, cmdline/config fragments, install scripts (idempotent)
├── models/                    # HEF fetch/build scripts + model cards (DFC runs on x86, never here)
├── calib/                     # Kalibr wrappers, targets, procedures
├── bench/                     # harnesses: sync-jitter, ptp-offset, per-stage profiler, versions.md, benchlog.md
├── datasets/                  # EuRoC/TUM-VI runners, baselines/ (committed numbers)
├── yocto/meta-garuda/         # recipes-in-waiting for the Hailo-15 product tier (see rule 6)
└── ci/                        # x86 build, unit, licence-scan (MUST fail on GPLv3)
```

## 5. Phase status & gates (tick as achieved; numbers go in bench/benchlog.md)

- [x] **P1 NPU sanity** — identify OK; model-zoo YOLO ≥30 fps on sample video; one LLM answering via hailo-ollama *(ticked 2026-07-29: identify HAILO10H FW 5.1.1; 75.95 fps end-to-end yolov8m; qwen2:1.5b answered — bench/benchlog.md)*
- [ ] **P2 capture** — CM3 streaming; later: GS pair triggered, inter-camera jitter <100 µs (LED-flash rig), 1 h no-drop at 30 fps
- [ ] **P3 time** — ptp4l stable; offset <50 µs RMS through switch (point-to-point sub-µs first); trigger generated from disciplined clock, pulse↔frame matching proven incl. dropped-frame case
- [ ] **P4 VIO** — Basalt on EuRoC headless first (record fps + per-stage ms on this Pi); live gate when GS pair arrives: ≥20 Hz front end, ≥200 Hz pose, <20 ms latency
- [ ] **P5 bridge** — MicroXRCEAgent up; odometry → EKF2 external vision on CA90; VIO-kill → covariance failsafe demonstrated
- [ ] **P6 load** — VIO + detection + VLM concurrent; no VIO frame deficit; no throttling (`vcgencmd get_throttled` = 0x0); thermals logged

## 6. Working rules for Claude Code (non-negotiable)

1. **Numbers are sacred.** Never invent, estimate, or "expect" a benchmark result into any doc, commit message, or code comment. Every performance figure must come from a harness run on this machine and be recorded in `bench/benchlog.md` with date + `versions.md` snapshot. If asked to fill a number you don't have, write `TBD-bench`.
2. **Artifacts before assumptions.** Before writing configs/drivers against hardware state, check it: `uname -a`, `lspci`, `ethtool -T eth0`, `libcamera-hello --list-cameras`, package versions. Record findings, then code.
3. **Idempotent scripts.** Everything in `hosts/pi5/` must be safe to re-run. No unguarded appends to cmdline.txt/config.txt — check-then-modify.
4. **Don't apply known-fixes pre-emptively** (ASPM off, Gen2 fallback, desc page size). Only on reproduced symptom, and log the symptom + fix in docs/decisions.md.
5. **Hardware safety:** never instruct or script anything that drives GPIO toward camera XTR without the 1.8 V level-shift path being explicit. Flag any 3.3 V-direct suggestion as an error.
6. **Licence gate:** before adding any dependency, state its licence in the commit/PR description. CI licence-scan failing is a hard stop, not an override. When a dependency lands, also drop a skeleton bitbake recipe (name/version/LICENSE/SRC_URI stub; unbuilt is fine) in `yocto/meta-garuda/` — recipes accrete one at a time so the Hailo-15 port inherits a half-built layer, not a greenfield.
7. **Small commits, tested.** Each service change lands with its unit test or harness invocation. If a test can't run without absent hardware, mark it `@requires_hw(camera_gs)` style and ensure CI skips cleanly.
8. **Structural review at midpoints:** after every ~10 files or a phase boundary, stop and summarise repo state vs this file; propose pruning before adding.
9. **Red-team on phase close:** before ticking a gate, write the strongest argument the result is wrong (thermal throttled? underpowered? wrong clock source? cherry-picked dataset?) and address it in benchlog.
10. **Sudo/system changes:** propose the command and what it modifies; for anything touching boot config, driver install, or partitions, wait for explicit go-ahead in-session.
11. **Never `apt upgrade` casually** — kernel bumps can break the hailo driver mid-sprint. Upgrades are a deliberate act: snapshot `versions.md`, upgrade, re-run P1 sanity, record.
12. **The FC is sacred:** any code that could reach PX4 (DDS/MAVLink) defaults to a simulated endpoint. Real-FC sessions are explicit, and never write parameters to the FC without listing them first.

## 7. Current immediate objective (updated 2026-08-10 — RESUME HERE)

**Bench state:** Pi (garuda@10.42.0.128 via eno1 pi-share; GarudaBench hotspot → 10.43.0.1) is
mounted on a **demo stand**, powered by the **official 27 W PSU (installed + verified 2026-08-10:
max_current 5000 mA, get_throttled 0x0 with mono hot, EXT5V 5.17 V — benchlog)**. The old
"keep mono stopped" constraint is retired. `garuda-mono` (:8098) + `garuda-portal` (:8080)
auto-start on boot.

**`feat/pose-gestures` — IMPLEMENTED, bench-verified 2026-08-10** (stacked on
feat/portal-tracking; final whole-branch review closed clean; spec:
`docs/superpowers/specs/2026-08-09-pose-gesture-control-design.md`). Mono has a third detector
mode `pose` (`POST /api/detector?mode=pose` — runtime switch, reverts to `primary` on service
restart): zoo `yolov8m_pose` HEF (AGPL eval-only; card `models/yolov8m_pose_h10.md` with a
twice-validated decode + shape-based head discovery, s-variant drop-in capable), 17 COCO kpts
ride ByteTrack into snapshot v2, dual subject/handoff GestureFSMs (symmetric hysteresis),
portal draws skeletons + gesture chip and acts server-side: STOP→follow paused, FOLLOW
ME→re-lock via mono_click ((name,track_id) onset dedup), POINT→HUD only. Gesture constants
FROZEN (per-rule confidence gating, owner-ruled): 2 m all-four verified zero-false; 4 m
follow_me verified; operating envelope = full upper body in frame. fps: pose adds no measurable
cost; the shared pipeline caps at ~10 fps in ALL modes (camera set 15) — see benchlog
2026-08-10 red-team.

**NEXT, in order:**
1. **Merge decisions (owner):** `feat/pose-gestures` → `feat/portal-tracking`, then that branch
   → master. Both unblocked (PSU verified). Branch is feature-complete, all committed.
2. **TBD-bench carryovers:** post-gating 4 m gesture re-verify (~3 min in front of the stand);
   y26d metric-depth physical validation (move-test + tape-measure, unchanged carryover).
3. **Shared-pipeline fps ceiling** (~10 fps vs 15 fps camera, mode-independent — measured
   2026-08-10): separate optimization workstream if the demo wants full camera rate.
4. **Camera tab decision** (unchanged): retire/repurpose vs real CM3 arbitration.
5. **Pre-Phase-8 security gates** (unchanged): portal auth; move off passwordless-sudo.
6. **Journald persistence proposal** (unchanged, needs rule-10 OK).

## 8. Glossary (project-specific)

- **CA90** — PIC32CZ CA90 MCU, the PX4 flight controller (separate board)
- **Garuda** — this SDK; also the team's dsPIC33 ESC project ("Garuda ESC") — different repo, don't confuse
- **Gate** — a measured pass/fail number defined in §5; gates decide phase progression
- **HEF** — Hailo executable format (compiled model); built on x86 with the Dataflow Compiler
- **PAB** — Pixhawk Autopilot Bus (FC module standard, product-tier context)
- **T1S** — 10BASE-T1S multidrop Ethernet (product-tier ESC bus; not on this bench)
- **XTR** — IMX296 external trigger pad, 1.8 V logic
