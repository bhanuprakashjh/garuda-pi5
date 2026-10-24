# Garuda SDK — Specification, Development Plan, BOM & Test Plan

**Version 0.1 draft · July 2026 · Embedded Solutions Engineering**
**Status: internal working document. All prices are estimates for planning; effort figures assume AI-assisted development as described in §6.**

---

## 1. What it is

Garuda SDK is the companion-computer software stack for the Microchip drone/robotics platform: the Linux-side services that turn a mission computer into an autonomy engine, paired over Ethernet with a PX4 flight controller on Microchip silicon (PIC32CZ CA90 class).

**Three hosts, one SDK:**

| Tier | Host | Accelerator | Role |
|---|---|---|---|
| Dev / eval | Raspberry Pi 5 / CM5 | Hailo-10H (AI HAT+ 2 bench, bare M.2 flight) | Prove pipeline, ship eval kit, VLM demos |
| Product | Hailo-15H SoM (SolidRun 47×30 mm) on Microchip carrier | integrated | Industrial tier, AI-ISP night VIO |
| High end (later) | PIC64HX | Hailo accelerator | All-Microchip, cert-adjacent |

**Positioning:** the only permissively-licensed, source-available, customer-extensible autonomy SDK in the sub-10 W class. ModalAI's QVIO is closed Qualcomm binaries; Airvolute ships no SDK; NVIDIA's accelerated cores are closed GEMs. Garuda is open where they are closed, and the accelerated kernels (Phase 3, Hailo DSP) are the retained moat.

**Licence policy (hard rule):** everything in the reference stack must be shippable inside a customer's closed product. BSD/MIT/Apache/LGPL-dynamic only. GPLv3 estimators (OpenVINS, VINS-Fusion, ORB-SLAM3) and the archived PX4-Avoidance are excluded. linuxptp (GPL daemon, process boundary) is acceptable.

---

## 2. Architecture

```
                 ┌──────────────────────── mission computer (Linux) ───────────────────────┐
 cameras ─CSI──► │ garuda-camera ─► garuda-vio ─► garuda-mapper ─► garuda-planner          │
 (HW trigger)    │      │              │               │                │                  │
 IMU (from FC    │      ▼              ▼               ▼                ▼                  │
  over DDS)      │ garuda-inference (HailoRT: detect / segment / depth / VLM)              │
                 │      │                                                                  │
                 │ garuda-bridge (uXRCE-DDS agent + MAVLink router)                        │
                 │ garuda-health · garuda-logger · garuda-portal · garuda-ota              │
                 └────────────┬────────────────────────────────────────────────────────────┘
                              │ Ethernet · PTP (switch = grandmaster) · uXRCE-DDS + MAVLink
                 ┌────────────▼───────────┐
                 │  PX4 on PIC32CZ CA90   │  own IMUs · own RTOS · own power rail
                 └────────────────────────┘
```

**Design rules (from the platform architecture):**
- Nothing in the inner control loop crosses Ethernet. Odometry (50–100 Hz + covariance) and obstacle data do; attitude/rate never leave the FC.
- Failsafe gates on odometry validity/covariance, never on link liveness. Loss of mission computer = degraded mode, not fallout.
- One PTP clock domain: camera trigger generated from the disciplined clock, strobe returned to a capture input, EKF external-vision delay measured, not guessed.
- IMU for VIO comes from the FC over DDS (synchronized, timestamped at source) — not local SPI on the Pi (RP1 SPI latency spikes under load).
- Every sensor input sits behind a plugin interface (`ICaptureBackend`, `IDepthSource`, `IFrontEnd`) so hosts and sensors are swappable.

---

## 3. Services and features

### v1.0 — "usable" (target: month 9–12)

| Service | Features | Built from | Licence |
|---|---|---|---|
| garuda-camera | libcamera/V4L2 capture, HW trigger out, strobe timestamp in, per-frame PTP timestamps, GS sensor support (OV9281 pair via Camarray; IMX296 via XTR), Bayer/mono paths | libcamera + custom | LGPL-dyn + BSD (ours) |
| garuda-vio | Stereo-inertial odometry 20–30 Hz front end, ≥200 Hz pose out via IMU propagation, covariance output, health/validity flags, online time-offset estimation | **Basalt (BSD-3)** | BSD |
| garuda-calib | Camera intrinsics/extrinsics + cam-IMU calibration workflow, guided capture UI, Allan-variance IMU characterisation | Kalibr (BSD), allan_variance_ros | BSD |
| garuda-inference | HEF model runner (detect/segment/depth), model manager, zero-copy frame path, VLM endpoint on Hailo-10H tier | HailoRT | ours BSD; HailoRT terms |
| garuda-mapper | Local TSDF/ESDF obstacle map from stereo or ToF depth | Voxblox (BSD) | BSD |
| garuda-planner | Local trajectory generation, goal-to-setpoint, stop-and-replan | OMPL / mav_trajectory_generation | BSD/Apache |
| garuda-bridge | uXRCE-DDS agent, odometry→EKF2 external vision, trajectory setpoints, MAVLink router alongside for GCS | eProsima agent, PX4 | Apache/BSD |
| garuda-time | ptp4l/phc2sys management, PHC-disciplined trigger generation, sync-quality monitor | linuxptp + ours | GPL daemon + BSD |
| garuda-health | Service supervision, watchdogs, degraded-mode signalling to FC | ours | BSD |
| garuda-logger | Flight-synchronised logging (images, odom, ULog cross-ref), export tools | ours | BSD |
| garuda-portal | Browser UI: status, calibration, config, live views, map view | ours (lightweight web) | BSD |
| garuda-ota | A/B image update with rollback; apt repo for services | ours | BSD |

### v1.5 — differentiation
- Depth-source plugins: Arducam T2 ToF (landing/indoor), learned stereo/mono depth on NPU
- Precision-landing service (down-cam marker + ToF fusion)
- Night-VIO profile (Hailo-15 AI-ISP tuned for feature stability; sub-0.01 lux)
- Multi-vehicle time transfer demo (shared PTP domain)
- Robotics profile: Rover/AMR bring-up, ROS 2 Nav2 integration

### v2.0 — the moat (conditional, post-close)
- DSP-offloaded front end: census/SGM, KLT pyramids, descriptor matching on the Hailo-15 Tensilica DSP (Cadence toolchain — vendor access)
- NPU-formulated CV kernels via Dataflow Compiler (fixed-weight networks)
- Perception-socket contract published as Dronecode standard proposal
- Patent candidates: ISP-partition architecture; ISP-for-VIO tuning; PTP trigger/strobe contract

**Explicit non-goals:** no RTOS partition on the companion (the FC is the RT domain); no GCS replacement; no closed forks of the permissive components.

---

## 4. Repository layout

```
garuda-sdk/
├── services/           # one dir per service above (C++/ROS 2 nodes)
├── interfaces/         # ICaptureBackend, IFrontEnd, IDepthSource, msg defs (the contract)
├── hosts/
│   ├── pi5/            # configs, overlays, install scripts, known-fix baked configs
│   ├── cm5/            # carrier deltas, flight image
│   └── hailo15/        # Yocto layer (Phase: port)
├── models/             # HEF build scripts (DFC on x86), model cards
├── calib/              # Kalibr wrappers, targets, guided workflows
├── bench/              # test harnesses: sync-jitter, PTP offset, per-stage profiler
├── datasets/           # EuRoC/TUM-VI runners, our recorded sets, regression baselines
├── ci/                 # x86 build+unit, cross-compile, dataset regression
└── docs/               # per-phase bring-up guides, API, licence audit
```

---

## 5. Development plan

### Phase 0 — Scaffold (before hardware arrives) — week 0–2
Repo structure, interfaces, Basalt cross-compile, EuRoC/TUM-VI harness, per-stage profiler, Phase-1 install scripts with vDMA/ASPM fixes pre-baked, garuda-time skeleton, bridge configs both sides (Pi + CA90). **AI share ~85%.**

### Phase 1 — Bench base + NPU — week 2–3
Pi 5 + AI HAT+ 2 up, `hailo-h10-all`, model-zoo smoke test, VLM hello-world. Gate: YOLO ≥30 fps, LLM tokens flowing. **AI ~90%; bench ~1 day.**

### Phase 2 — Synced stereo — week 3–6
Camarray stereo capturing; IMX296 XTR path wired (1.8 V!, R11 rework) and validated; SensorTimestamp monotonic; **gate: inter-camera jitter <100 µs**, no drops at 30 fps for 1 h. **AI ~70%; the wiring and scope work is yours.**

### Phase 3 — Time base — week 4–7 (overlaps)
ptp4l/phc2sys to LAN966x grandmaster; PHC-disciplined trigger; strobe loopback measured. **Gate: PTP offset <50 µs RMS through switch (point-to-point sub-µs sanity first); trigger→exposure latency characterised.** **AI ~75%.**

### Phase 4 — VIO — week 6–10
Calibrate (garuda-calib), run Basalt live. **Gate (the big one): ≥20 Hz stereo front end + ≥200 Hz pose + <20 ms latency on Pi 5 with NPU idle; record margin with NPU under load.** Then EuRoC ATE within 2× of published Basalt desktop figures. **AI ~80% of code; number comes off the bench.**

### Phase 5 — Bridge + closed loop — week 8–12
Odometry into EKF2 external vision on the CA90; position hold indoors; failsafe-on-covariance demonstrated (kill VIO mid-hover → controlled degradation). **AI ~85%.**
**Note: CA90 PX4 Ethernet/PTP driver state is a prerequisite — verify early (front-load per research caveat).**

### Phase 6 — Concurrent load + flight prep — week 10–14
Detection + VLM concurrent with VIO; thermal/power soak; brownout margin on 5 V rail; move to CM5 + carrier + bare M.2 10H. **Gate: no VIO degradation with NPU at duty; 1 h soak without throttle below target clocks.** **AI ~60%.**

### Phase 7 — Flight — week 12–16
Outdoor GPS-denied hover + waypoint; published logs. First public demo asset. **AI: flight cards, log analysis (~50%).**

### Phase 8 — Productise v1 — month 4–9
Portal, logger, OTA, docs, licence audit, apt repo, eval-kit image. **AI ~80%.**

### Phase 9 — Hailo-15 port — month 6–12 (parallel from month 6)
Yocto layer on SolidRun BSP, capture backend swap, HEF recompile, re-run Phase 4 gates on A53s → measured deficit sizes the v2 DSP work. **AI ~65% (BSP work is more bench-coupled).**

**Team sizing (with AI assist as below): 2 engineers core + 1 part-time hardware/bench through Phase 7; +1 for productisation; Hailo-15 port reuses the same pair. Without AI assist, double it.**

---

## 6. AI-assisted development model

**Where AI (Claude) carries the load:** all service code first drafts, configs with known-fixes baked in, cross-compile/build systems, test harnesses and analysis tools, calibration tooling, documentation (~90%), log/ULog analysis, licence audit, CI. Measured overall: ~75% of Phases 0–8 software authored by AI, reviewed by engineers.

**Where humans are irreplaceable:** everything touching hardware (wiring, scope, thermal), every empirical number (fps, jitter, watts, °C), flight operations, ISP tuning judgement, architecture sign-off, and all of v2's DSP kernel work on the Cadence toolchain (AI drafts portable-C references and offload specs only).

**Working rules (lessons from this programme):**
1. **Artifacts before code:** feed the real `uname -a`, package versions, exact camera SKUs, CA90 driver state before configs are generated. Never build against assumptions.
2. **Repo, not fragments:** AI output lands as reviewable commits with tests, per-phase READMEs.
3. **Numbers are sacred:** AI never invents a benchmark; every gate value comes from the bench and is recorded in `datasets/baselines/`.
4. **Structural review at midpoints,** not only at the end.
5. **Red-team on demand:** each phase ends with an explicit "argue why this phase's result is wrong" pass.

---

## 7. Bill of materials

### Bench (Phase 1–5) — order now

| Item | Qty | Est. | Notes |
|---|---|---|---|
| Raspberry Pi 5, 8 GB | 2 | $80 ea | second unit = CI/soak |
| Active Cooler | 2 | $5 ea | mandatory |
| 27 W official PSU | 2 | $12 ea | 5 V/5 A hard requirement |
| **AI HAT+ 2 (Hailo-10H, 40 TOPS, 8 GB)** | 2 | $130 ea | bench accelerator |
| Arducam OV9281 stereo + Camarray HAT | 1 | ~$120 | day-one synced stereo (mono GS) |
| Raspberry Pi GS Camera (IMX296) | 2 | $50 ea | flight-representative trigger path (R11 rework, 1.8 V XTR) |
| C/CS lenses for GS cams | 2 | ~$25 ea | match FoV to OV9281 |
| Arducam T2 ToF kit | 1 | $300 | mapper/landing workstream (CAM1) |
| USB camera with on-board H.264 | 1 | ~$60 | recording path — zero CPU cost |
| SPI IMU breakout (ICM-42688) | 1 | ~$20 | bench-only cross-check; flight IMU comes from FC |
| microSD (A2) + NVMe/eMMC | — | ~$60 | logging headroom |
| LAN9668-class TSN switch EVB | 1 | internal | PTP grandmaster — from Microchip stock |
| PIC32CZ CA90 FC board | 1 | internal | your PX4 target |
| Bench PSU, scope access, misc cables/FFC | — | ~$100 | |
| **Bench subtotal** | | **≈ $1,300 external** | |

### Flight prototype (Phase 6–7)

| Item | Qty | Est. | Notes |
|---|---|---|---|
| CM5 8 GB / 64 GB eMMC (no wireless variant for defence-adjacent) | 2 | $195 ea | −20/+85 °C — note not −40 |
| CM5 IO board (bring-up) | 1 | ~$25 | |
| Compact dual-CSI + M.2 carrier (Waveshare CM5 base class; **verify Hailo M.2 compat** — Geekworm X1502 reported not working) | 1–2 | ~$60 | carrier selection = named risk |
| **Bare Hailo-10H M.2 2242, 8 GB** | 2 | ~$195 ea | sourcing friction expected — order early via Mouser/AAEON |
| 5 V ≥6 A regulator from vehicle bus | 1 | ~$30 | brownout margin |
| Test airframe (existing quad, 6S) + your ESC/power boards | — | internal | |
| **Flight subtotal** | | **≈ $900 external** | |

Entire programme hardware: **under $2,500 external spend.** (Comparison point: one VOXL 2 is $1,200 — buy one anyway for the benchmark table.)

---

## 8. Testing plan

### 8.1 Continuous (CI, x86 + cross)
Unit tests per service; interface-contract tests (msg schemas, QoS); build on x86 + aarch64 cross; static analysis; licence-scan gate (fails on GPLv3 ingress).

### 8.2 Dataset regression (nightly)
EuRoC MH01–05, V101–203 + TUM-VI room/corridor through garuda-vio headless. Metrics: ATE RMSE, RTE, front-end Hz, per-stage ms. **Regression gate: no metric worsens >5% vs stored baseline.** Our own recorded stereo+IMU sets join the pool from Phase 4 onward.

### 8.3 Bench rigs (hardware-in-loop)
- **Sync rig:** LED flash visible to both cameras + strobe capture → inter-camera and trigger→exposure jitter distributions. Gate <100 µs.
- **Time rig:** PTP offset logging vs grandmaster, 24 h, through switch and point-to-point; Allan deviation of disciplined clock. Gate <50 µs RMS (switched).
- **Load rig:** VIO + detection + VLM concurrently; CPU/GPU/NPU utilisation, thermals, 5 V rail current (scope for spikes), 1 h soak. Gate: no VIO frame deficit, no throttling below target clock, no brownout at stated regulator.
- **Fault-injection:** kill each service mid-run; camera unplug; PTP loss; verify garuda-health signals FC and FC failsafe engages on covariance, not link.

### 8.4 Closed-loop (Phase 5+)
Indoor position hold: drift <0.5 m over 5 min, VIO-only. Setpoint tracking error bounds. Mid-hover VIO kill → controlled descent/hold on FC-only, logged.

### 8.5 Flight test cards (Phase 7)
Card 1 hover GPS-denied 5 min · Card 2 square pattern 20 m legs · Card 3 return-to-start on VIO · Card 4 night hover (Hailo-15 tier later) · each with ULog + garuda-logger bundle published internally. Safety: geofence, pilot override, kill switch, ballistic-safe test area.

### 8.6 Comparative benchmark (feeds the pitch deck)
Same flight cards on VOXL 2 (purchased unit) where feasible; publish methodology + results including rows we lose — consistent with the ESC benchmark philosophy: beat "good enough" with measured data, not spec sheets.

### 8.7 Port-parity (Phase 9)
Re-run 8.2–8.4 on Hailo-15H; delta table vs Pi baseline = the input that sizes (or cancels) the v2 DSP offload.

---

## 9. Risks & open items

| Risk | Mitigation |
|---|---|
| CA90 PX4 Ethernet/PTP driver immaturity | Front-load verification week 1 (research flagged as schedule risk) |
| Basalt Hz on A53 (port) | Pi-first strategy converts unknown → measured deficit; DSP offload as sized response |
| Bare 10H M.2 sourcing at low qty | Order week 1; AI HAT+ 2 covers bench regardless |
| Carrier Hailo-compat traps | Buy two candidate carriers; test before committing |
| Cadence Xtensa licence transfer terms | On the pre-close question list — linchpin of v2 |
| Hailo-15 mono-GS ISP support | Pre-close technical question; fallback = bypass ISP for VIO path |
| GPL ingress by contributor habit | CI licence gate + audit doc |
| Scope creep | v1 feature list is frozen by this doc; additions go to v1.5 |

---

## 10. What ships, when (summary)

| Milestone | When | Artifact |
|---|---|---|
| Repo + Phase 0 scaffold | week 2 | garuda-sdk repo, CI green |
| NPU + stereo + PTP on bench | week 7 | jitter/offset numbers published |
| **VIO go/no-go numbers** | week 10 | gate report |
| GPS-denied hover, closed loop | week 12–14 | logs + video |
| Outdoor flight demo | week 16 | the demo asset |
| v1.0 eval-kit image | month 9 | flashable image + docs + apt repo |
| Hailo-15 parity report | month 12 | port + delta table → v2 decision |
