# docs/decisions.md — decisions log

Format: date, decision, evidence, alternatives rejected. Newest first.

---

## 2026-08-09 — Power root-cause confirmed; supply contract inadequacy (measured, CLOSED)

**Symptom:** Four hard power-cuts on 2026-08-09 during Tracking-tab portal testing (fresh boots at
15:02:09, 15:51:57, 16:04:23, one more mid-verification). Direct cause-effect observed twice: opening
the Tracking tab (mono NPU+camera load ramp) → brownout within minutes. Browser connection drop or hard
reboot; Pi fully unreachable for 21 s to 33 min per outage.

**Evidence (bench-measured, 2026-08-09):**
- PSU max current: negotiated contract **3000 mA** (read from `/proc/device-tree/chosen/power/max_current`);
  charger label **5 V⎓3 A** (60 W GaN unit, but 60 W = 20 V×3 A, unusable by the Pi on the 5 V leg).
- EXT5V_V rail voltage at idle: **4.955 V** (`vcgencmd pmic_read_adc`).
- Under-voltage flag `get_throttled = 0x50000` (bit 16) observed within minutes of boot when mono active.
- PSU = 15 W ceiling (5 V × 3 A); no headroom for sustained NPU+camera load.

**Verdict:** root cause is **the supply profile, not cable/network/software.** The 5 V/3 A supply is
marginal; it sags under sustained load, triggering under-voltage throttle, followed by hard reboot.
This explains the repeating 2-hour crash pattern from the 2026-08-07 update, and all brownouts this
session.

**Fix pending:** official **Pi 5 27 W (5 V⎓5 A) USB-C PD supply** (owner purchasing). After install:
re-run any measurements captured under a logged `0x50000` (most of Task 7's fps/latency figures from
2026-08-07 branch; also the TUM-VI benchlog 2026-08-02 entry). Confirm `get_throttled = 0x0` under load
before trusting the new numbers.

**WARNING (workaround rejected):** do **NOT** set the EEPROM `PSU_MAX_CURRENT=5000` workaround — it
suppresses the throttle flag but deepens the rail sags by further reducing headroom. The flag is the
diagnostic, not the problem.

**Follow-up finding (proposed, not implemented):** portal's `MonoClient` holds a permanent SSE
subscription to mono's `/api/events`, and SSE subscribers count as viewers — so mono never idles while
the portal runs (goes hot at boot with nobody watching). Proposed fix: lazy-subscribe `MonoClient` only
while a browser client is connected (mirrors the existing lazy-`CameraStream` pattern). Until then, the
new Tracking-tab mono power button (deployed 2026-08-09) is the manual control.

## 2026-08-02 (latest) — GPLv3 rule RELAXED for bench/experimental (owner directive)

**Directive (owner, in-session):** the CLAUDE.md §1/§6 hard "no GPLv3" rule is **relaxed for now** —
GPL-3.0 / AGPL code + models may be used for **bench, experimental, and evaluation** work. Rationale
holds legally: running software locally is not *distribution*, so no copyleft obligation triggers.
This unblocks trying **ORB-SLAM3, OpenVINS, VINS-Mono/Fusion** (all GPLv3) for the VIO/SLAM
workstream, and GPL depth models (e.g. **scdepthv3**) as real options rather than eval-only.

**Guardrails (keep it reversible — this is the whole point of "for now"):**
- GPL components stay **behind the plugin interfaces** (`ICaptureBackend`/`IFrontEnd`/`IDepthSource`)
  and are **tagged `GPL-3.0 — not product-cleared`** (as `models/LICENCES.md` does), so a product build
  can swap a clean implementation without a rewrite.
- Do **not** deep-wire GPL into shippable `services/` in a way that's costly to remove.
- **Product-tier licence-clean remains the goal** — revisit before Phase 8 (eval image) / product.
  Shipping a GPLv3-encumbered SDK is still a deliberate, unresolved decision, not a default.
- CI licence-scan: keep it reporting GPLv3 (visibility) but no longer a hard build-stop for bench work.

## 2026-08-02 — Depth in the portal: integration working; SGBM accurate, NPU depth off (checkpoint)

**Status:** the portal **Depth tab is live and working** — SGBM / StereoNet / fast_depth / scdepthv3
switchable from the browser, embedding the `garuda-depth` service (`:8097`) in an iframe. Engine
switching is **stable**: a switch writes `~/.garuda-depth-model` + `os._exit(0)`, and the unit
(`Restart=always`) brings up a fresh process — a **full process death is the only thing that
reliably resets a wedged Hailo** on this HailoRT (in-process reconfigure → HAILO_INTERNAL_FAILURE;
`os.execv` keeps the wedged device fd; ~9 s/switch). Follow GUI untouched (`portal.py` unchanged, 72
portal tests pass). `install.sh` installs/enables the unit (rule 3).

**Depth accuracy verdict (bench-confirmed on-rig):**
- **CPU SGBM = accurate / metric ("bang on")** — the production depth engine. It *measures* true
  distance (verified 12.5 in → 32 cm; consistent at range ~225 cm). Licence-clean (OpenCV).
- **NPU learned models = dense but OFF.** StereoNet (stereo) range-collapses indoors (driving-trained,
  our 47 mm baseline at 1–3 m is out-of-distribution). fast_depth / scdepthv3 (mono) are learned
  guesses — SGBM-calibrated to cm, structure-plausible, but **not measurements**, so not trustworthy
  for ranging.

**Takeaway:** SGBM is the metric depth for follow-range / obstacles. Accurate NPU depth would need a
**rig-suited, licence-clean model** (fine-tune on our short-range stereo → DFC compile); the eval
zone (`models/LICENCES.md`) is set up to chase that. NPU depth models stay bench/eval-only.

## 2026-08-02 — models/ EVALUATION ZONE + NPU depth-model bake-off

**Convention (owner-directed):** `models/` is an **evaluation zone** — ANY-licence model may be
fetched + run on the bench to benchmark it, because **evaluation is not distribution** (GPL
obligations trigger only on shipping). The wall: `models/LICENCES.md` tags each model
ship-eligible vs EVAL-ONLY; only clean-licence (BSD/MIT/Apache/LGPL-dyn) models graduate into
`services/`; CI licence-scan polices `services/`, not `models/`. Keeps CLAUDE.md rule 6 intact
(clean `services/`) while letting us try the whole zoo, like the detection models.

**Depth bake-off (all Hailo-10H, `:8093`–`:8096`):**
| model | task | licence | ship | on-bench | verdict |
|---|---|---|---|---|---|
| SGBM (CPU) | stereo | OpenCV | ✅ | ~10 fps, 66%, **metric** | the ranging engine |
| stereonet | stereo | none | ❌ | 14 fps, dense | driving-trained, range-collapses |
| fast_depth | mono | **MIT** | ✅ | 221 fps | indoor, decent structure, relative scale |
| scdepthv3 | mono | GPL-3.0 | ❌ | 135 fps | **best structure** (RMSE 0.48), eval-only |

**Takeaways:** (1) SGBM stays the *metric* depth (only one that measures). (2) Of the NPU learned
models, **scdepthv3 looks best but is GPL-3.0 (eval-only)** → signal to find/train a clean-licence
indoor mono model; **fast_depth (MIT)** is the shippable-but-weaker option. Runners:
`hosts/pi5/hailo_mono_depth.py`, `camarray_depth_mono.py` (any mono HEF via `--hef`).

## 2026-08-02 — Stereo depth on the NPU (StereoNet HEF) — bench experiment ✅ (licence-flagged)

**Result:** stereo depth now runs on the **Hailo-10H** via the **StereoNet** HEF from the Hailo
Model Zoo — dense/smooth depth at **~13.8 fps**, CPU only ~64% / 46 °C (matching offloaded to the
NPU). `http://<pi>:8094/`. Head-to-head vs CPU SGBM (`:8093`): 100% dense vs 76%, 13.8 fps vs ~2.

**How:** pre-compiled HEF (`models/fetch_stereonet.sh`, no x86 DFC needed) → HailoRT **InferModel**
API (`create_infer_model → configure → create_bindings → run`; the old VStreams `configure` path
returns HAILO_NOT_IMPLEMENTED on 5.1.1). I/O: 2× UINT8 368×1232×3 (rectified L/R) → 368×1232×1
disparity. `hosts/pi5/hailo_stereo.py` (`HailoStereoNet`) + `camarray_depth_npu.py` (viewer).
Rectify on CPU (our calibration) → StereoNet on NPU → `Z=f·B/d`. `swap=False` gave correct
near=warm on the first try (input_layer1 = left).

**⚠️ LICENCE (rule 6):** StereoNet source (nivosco/StereoNet) is **UNLICENSED** — bench-only, NOT
product-cleared; no yocto recipe added. Product must swap in a licence-clean depth model. The `.hef`
is git-ignored (fetch, don't commit). See `models/stereonet_h10.md`. Trained on driving/KITTI →
indoor accuracy rough, absolute scale provisional.

## 2026-08-02 — Dense stereo depth WORKING; outlier rejection + coverage were the unlocks ✅

**Result:** live stereo depth is now dense and coherent — **76% valid pixels**, clean
fore/background separation (person in warm/near, room in cool/far), metric center readout
(~194 cm on the wall). `http://<pi>:8093/`. The L/R swap fix + these two got there:

1. **Outlier rejection in the calibrator.** 68 raw pairs gave a *broken* stereo RMS of **23 px**
   — tilted boards trigger the checkerboard 180° flip ambiguity, scrambling L/R corner order in a
   subset. `stereo_calibrate.py` now fits → drops pairs with per-pair error > max(1px, 3×median)
   → refits, iterating (`_stereo_view_errors`). Dropped **17** → **51 inliers** → RMS **23 → 1.26 px**.
2. **Coverage buys density, not RMS.** RMS plateaued at ~1.26 px (same as the old 13-pair run) —
   the ceiling is now **image sharpness** (varLaplacian ~400–640 = soft; focus/flatness/gain-noise),
   not data quantity. But 51 well-spread pairs give **uniform rectification across the FoV**, so
   depth density jumped **31% → 76%**. Coverage helped even though it didn't move the RMS number.

**Optional push to RMS <0.5:** sharper board images — check/adjust lens focus for ~0.7 m, mount the
target dead-flat on rigid backing, lower gain + more light. 1.26 px is already usable.
**Still provisional:** absolute scale (`square=33.5 mm`) — pin with a known-distance check.

## 2026-08-01 — Depth was sparse from a LEFT/RIGHT SWAP, not calibration quality (fixed + verified)

**Finding (user was right — not just calibration):** the live depth was sparse/non-corresponding
because the stereo pair is fed to SGBM **backwards**. The calibration proves it:
`T[0] = +40.7 mm` (positive), `Q[3,2] = -1/Tx < 0`, `P2[0,3] = +46480 > 0` — all the *opposite*
sign of a standard left-reference rig. So **tile0 (left half of the combined frame) is the
physically RIGHT camera; tile1 is the LEFT.** `StereoSGBM(rect_tile0, rect_tile1)` then yields
**negative** disparity for real objects, which `disp > 0` masks away → sparse speckle.

**Fix (committed, verify on next boot):** `StereoRig.reference_is_cam2 = (P2[0,3] > 0)` and
`order_for_matching()` swaps the pair so SGBM gets (left=tile1, right=tile0) → positive
disparity; the depth view displays the reference (tile1) it's aligned to. Unit-tested
(`tests/test_stereo_rig.py`). Calibration itself is unaffected (stereoCalibrate is naming-agnostic;
RMS 1.22 is a separate quality issue). **On resume:** relaunch `camarray_depth_view.py` and
confirm the depth densifies; the RMS-1.22 noise remains until a tighter recalibration.

## 2026-08-01 — Stereo depth pipeline live end-to-end; PROVISIONAL calibration (resume point)

**State at power-down (resume here):** the whole stereo-depth chain runs on the bench —
capture → calibrate → rectify → StereoSGBM → colorized live depth view — but on a *rough*
calibration, so depth is currently sparse/noisy. Milestone (pipeline works) reached; quality
gated on a better calibration.

**What was built (all committed):**
- `hosts/pi5/camarray_capture.py` — browser calibration-capture tool (live corner overlay,
  saves raw L/R pairs to `calib/captures/`). `camarray_view.py` — plain stereo preview.
  `camarray_depth_view.py` — live rectify+SGBM depth view (port 8093).
- `calib/stereo_calibrate.py` — `findChessboardCornersSB`→`stereoCalibrate`→`stereoRectify`,
  writes `bench/camarray_stereo.yaml` + RMS; `services/garuda-camera/stereo_rig.py` loads it.
- `camarray_setup.sh` now also locks exposure/gain and (opt) caps fps via `vertical_blanking`.

**Findings (Rule 2/9):**
1. **Board is 9×6 inner corners (10×7 squares), NOT 11×8.** The hand-count of "12×9 squares"
   was wrong; `findChessboardCornersSB` on a real held-up frame only locks at (9,6). Use
   `--cols 9 --rows 6`. `square_size` is provisional **33.5 mm** (single-cell measure; it's a
   pure scale factor — pin exactly later with a known-distance check, Rule 1).
2. **Cooling is PASSIVE** (heatsink, no fan — CLAUDE.md §2 corrected). A flat-out full-res
   corner-detect loop hit **267% CPU / 84.5 °C / freeze**; fixed by throttling detect to 2 Hz,
   central single-encode MJPEG, and capping sensor fps (`GARUDA_CAM_VBLANK≈3600`, ~10 fps).
3. **Provisional calibration:** 13/17 usable pairs, **stereo RMS 1.22 px** (target <0.5),
   **baseline 40.9 mm** (physically sane → geometry locked), focal ~836 px (K1) — at
   square=33.5 mm. RMS too high → poor rectification → sparse depth.

**Next (resume):** latch the capture tool's green indicator (2 Hz detect made it flicker),
re-shoot **30–50** well-spread sharp pairs (fill frame, all corners, tilts, still), re-calibrate
to RMS <0.5, and the existing depth view densifies. Then: known-distance scale pin; optionally
move the depth matcher onto the Hailo (HEF) to offload CPU (product-tier direction).

## 2026-08-01 — CamArrayBackend implemented + verified live through the plugin path ✅

**Result:** `hosts/pi5/camarray_backend.py` is now a real `ICaptureBackend` (was a stub). It
runs `camarray_setup.sh`, then streams the combined frame raw via a `v4l2-ctl --stream-mmap
--stream-to=-` subprocess; a reader thread slices fixed-size frames and emits `FrameMeta` +
`memoryview`. Verified **on-bench through the actual backend** (not just `v4l2-ctl` by hand):
4 frames, each exactly **4,096,000 B** (5120×800×1 GREY); `plan_split(5120,800,1,4)` +
`crop_row_major` split gives **tile 0 mean 36.2 / max 255, tile 1 mean 36.2 / max 255**
(the live stereo pair — equal means = same scene), tiles 2/3 all-zero (unpopulated). Runs as
the `garuda` user with **no sudo** (user is in the `video` group; `media-ctl`/`v4l2-ctl` work).

**Bug found + fixed (Rule 4 log):** the setup script's sensor-name regex
`grep -oE "arducam-pivariety [0-9-]+"` dropped the trailing `c` of the entity name
`arducam-pivariety 10-000c` (i2c addr 0x0c → 4-hex-digit suffix `000c`). media-ctl then
rejected the truncated name on `-V`/`-e` with `Unable to setup formats: Invalid argument (22)`.
My earlier *manual* commands used the full name so never hit it; the script's extraction was
the defect. Fix: `[0-9a-z-]+` so the hex suffix is captured. `read_exact` (the frame-slicer)
is pure and unit-tested (`services/garuda-camera/tests/test_camarray_backend.py`).

## 2026-08-01 — CamArray WORKING all-Trixie via stock arducam-pivariety + raw V4L2 ✅

**Result:** the OV9281 CamArray is fully up on **this Trixie bench** — no Bookworm, no Arducam
installer, no libcamera swap, CM3/portal/follow untouched. Captured a real **5120×800 Y8**
combined frame; the two connected cameras populate **tiles 0 and 1** (mean ~111/112, live
image), tiles 2/3 empty. That's a synchronized stereo pair, raw.

**What actually cracked it (three things, in order):**
1. **Hardware, not OS** — the HAT needs its **USB power** (CSI ribbon can't power the FPGA
   bridge), and the **CSI ribbon had to be reseated** (a loose ribbon left the ArduChip's
   i2c dark). The berg SDA/SCL wiring reached only an EEPROM (`0x51`), a red herring; the
   ArduChip is at **`0x0c` on the CSI i2c (bus 10)**, reached over the ribbon.
2. **Stock driver exists** — the Trixie kernel already ships `arducam-pivariety.ko` + `.dtbo`.
   `dtoverlay=arducam-pivariety,cam0` (CSI0 + i2c_vc) binds it; it reads the ArduChip
   (firmware `0x10004`) and reports the combined sensor format.
3. **Raw V4L2, not libcamera** — stock libcamera has no pivariety CamHelper, so it won't list
   the camera; capture raw from `rp1-cfe-csi2_ch0` (`/dev/video0`) instead. The one gotcha:
   the `csi2` source pad feeds both the raw node and the ISP front-end (`pisp-fe`,
   MUST_CONNECT) — leave the ISP branch enabled and `STREAMON` fails "Broken pipe"; **disable
   `csi2:4→pisp-fe`, keep `csi2:4→csi2_ch0`**, set `Y8_1X8/5120x800`, capture GREY.

**Artifacts:** `hosts/pi5/camarray_setup.sh` (the media-ctl pipeline recipe), config in
`docs/camarray-bringup.md`. Supersedes the two entries below (stock ov9281 fails; installer
breaks Trixie; Plan-B capture-replay) — all moot now. Next: wire the recipe + raw capture into
`services/garuda-camera` `CamArrayBackend`, split 5120×800 → tiles 0/1 → stereo depth.

## 2026-08-01 (later) — CamArray ArduChip reached on-bench; bring-up via capture-and-replay (Plan B)

**Milestone:** after wiring, the CamArray bridge is alive and addressable. Root blocker was
**hardware, not OS**: the HAT needs (1) **USB power** (CSI ribbon + berg pins don't power the
FPGA bridge — LED lit but bridge under-powered), and (2) its **SDA/SCL berg holes wired to the
Pi's VideoCore i2c** — **pin 27 (ID_SD/GPIO0) / pin 28 (ID_SC/GPIO1) + GND** = `/dev/i2c-0`.
Then the ArduChip answers at **i2c `0x51` on bus 0**, chip ID `0x82 0x47 0x03`.

**It's a Pivariety-style controller (16-bit register protocol)** — register map from
`ArduCAM/Camarray_HAT` (DEVICE `0x0100` / PIXFORMAT `0x0200` / FORMAT `0x0300` / CTRL `0x0400`
/ SENSOR `0x0500` banks). So the stock `ov9281` driver (8-bit sensor protocol on the CSI bus)
can **never** drive it, and Arducam's installer is broken on Trixie 2026 (entry below).

**Decision — Plan B (capture-and-replay), CM3-safe:** capture the ArduChip init that Arducam's
working stack emits on a **Bookworm** card (kernel i2c ftrace), then **replay it on Trixie** via
`i2ctransfer` (`hosts/pi5/camarray_replay.py`) + raw-V4L2 capture into `services/garuda-camera`
— **never swapping the system libcamera**. Procedure: `docs/camarray-i2c-capture.md`.

**Alternatives rejected:** Arducam installer on this bench (breaks the CM3/libcamera stack the
whole project runs on — proven below); hand-guessing the 16-bit init blind (risky, unknown map).

## 2026-08-01 — Arducam CamArray (OV9281 ×4, B0267) NOT bring-able on Trixie yet; deferred to a Bookworm card

**Decision:** the OV9281 CamArray HAT stays on the roadmap but is **not brought up on this Trixie bench**. Do it on a separate Raspberry Pi OS **Bookworm** SD card (Arducam-supported), isolated from the working portal/follow stack.

**Evidence (reproduced on-bench, connected on CAM1 alongside CM3 on CAM0):**
- HAT answers on i2c (`ov9282 10-0060` probes), so hardware + wiring are fine; global-shutter, hardware-synced.
- **Stock `ov9281`/`ov9282` driver cannot power it:** `fail to write MIPI_CTRL00` → `failed to power-on the sensor` → `probe ... failed with error -5`. The kit's OV9281 are the *special CamArray* version (HAT bridge), not stock-driver-compatible.
- **Arducam's installer (`install_pivariety_pkgs.sh`) is built for an older OS:** it `apt remove libcamera0` + `apt purge libpisp0.0.1`, but Trixie ships **`libpisp1 1.6.0`** (name mismatch). The run **hung and was killed**, never installed the Arducam fork, and drifted the stock libs (`libcamera` → 0611-2, `libpisp` ↓ 1.5.0).
- **Recovery worked cleanly:** pre-snapshotted 50 stock camera `.debs` (`~/camera-stock-debs/` on the Pi) + `config.txt.pre-camarray`; `dpkg -i --force-downgrade` restored `libcamera 0609-1` / `libpisp 1.6.0-1`, config to auto-detect, reboot → CM3 + portal + follow all green (210/210 detections/follow).

**Cam0/cam1 mapping (Pi 5, this board):** CM3 (imx708) is on the port the overlays call **`cam1`** (i2c@80000/csi@128000); the free/CamArray port is **`cam0`** (i2c@88000/csi@110000) — the reverse of the silkscreen intuition. Record when wiring overlays.

**Alternatives rejected:** (a) run Arducam's installer on this working bench — high risk of an unrecoverable libcamera/libpisp swap breaking the CM3/portal/follow stack the whole project runs on (rule 3/10); (b) stock `ov9281` overlay — proven to fail at the driver level. See `docs/camarray-bringup.md`.

## 2026-07-30 — garuda-portal V1 security posture (bench vs product tier)

**Decision/finding:** The portal/configd privilege split (unprivileged `garuda-portal`
PUTs config and requests unit actions over a Unix socket; only root-run `garuda-configd`
whitelists and executes them) is architectural intent, **not an enforced containment
boundary on this bench**. `hosts/pi5/install.sh` grants the bench user
`ALL=(ALL) NOPASSWD: ALL` sudo (step [2/5], restoring what Trixie stopped granting by
default) so that the SAME account running the unprivileged portal can already run
anything as root directly — the socket/whitelist indirection buys nothing against that
account on this machine. It remains real, load-bearing containment at product tier,
where the deployed account must NOT hold sudo.

**Evidence:** `sudoers.d/010-${USER}-nopasswd` created unconditionally by install.sh;
`garuda-portal.service` runs `User=garuda` — the identical account install.sh just gave
passwordless root. Confirmed against the whole-branch review 2026-07-30 (this fix wave
also closed F1/F2: `probes.WATCHED_UNITS` pointed at a decommissioned unit and a non-unit
process, and `/opt/garuda/garuda-configd` was garuda-owned code executed by root — now
root:root, `go-w` stripped, see fix-wave report).

**Two HARD gates before Phase 8 or any exposure off this bench (not deferrable minors):**
1. **Authentication on the portal.** It is unauthenticated on `0.0.0.0:8080` — any host
   on the LAN can `PUT /api/config` and start/stop `garuda-*` units with no credential.
   Acceptable only because this bench sits on an isolated dev LAN with no untrusted peers.
2. **Portal run as a non-sudo account.** Product-tier deployment MUST run
   `garuda-portal` (and ideally the whole bench user) without `NOPASSWD: ALL`, or the
   configd privilege split above is decorative there too.

Both are tracked here rather than as code changes in this fix wave — they need a design
decision (auth scheme; account model) out of scope for a fix dispatch, per the
whole-branch review 2026-07-30.

## 2026-07-30 — Basalt source: mateosss/basalt fork (Monado-maintained), built from source on Trixie

**Decision:** `garuda-vio` builds on the Monado-maintained Basalt fork
(`gitlab.freedesktop.org/mateosss/basalt`, BSD-3, active releases — latest 2026-07-24 with a
Pi 5 tuned `rpi5` cmake preset), compiled from source on the bench.

**Evidence:** The canonical TUM GitLab (`gitlab.vision.in.tum.de/tum-vision/basalt`) now
refuses anonymous clones (upstream effectively gone); the GitHub mirror is frozen ~2022 and
untested against GCC 14. The fork publishes prebuilt Pi 5 debs, but they target Bookworm —
on Trixie the binary wants 13 Bookworm-era sonames (boost 1.74, OpenCV 406, fmt9); installing
that library zoo is the repo-mixing we already rejected. Source build on Trixie is ABI-native
and matches the production configuration (garuda-vio runs native, not containerised).

**Licence:** BSD-3-Clause, unchanged from upstream — passes the gate.

## 2026-07-29 — ROS 2: Jazzy in Docker on Pi OS; keep ROS thin

**Decision:** ROS 2 Jazzy (not Humble), run in Docker on the Pi OS host. Native side keeps
garuda-camera/-time, HailoRT inference, and Basalt (plain C++, thin wrapper — never ROS-ified
internally). Container side gets the ROS nodes (VIO wrapper, mapper, planner), px4_msgs, rviz,
rosbag2. Boundary plumbing: `--network=host --ipc=host`, CycloneDDS pinned as RMW in both
worlds. Container images build only from a committed Dockerfile in `hosts/pi5/`.

**Rationale:** Jazzy is current LTS (2029, matches programme life) and works with PX4
v1.15/1.16 uXRCE-DDS; Humble targeted Ubuntu 22.04 bases and was the misfit that forced the
question. MicroXRCEAgent itself is a standalone binary — ROS buys the ecosystem (typed
px4_msgs, Nav2/OMPL wrappers, rosbag2 for the §8.2 regression datasets), so the middleware
stays a thin, containerised, portable layer — exactly the packaging seam the Hailo-15 port
wants. Camera-free milestone this enables: PX4 SITL in a second container + MicroXRCEAgent =
full Phase 5 bridge (odometry→EKF2, failsafe) proven with EuRoC replay before cameras/CA90
arrive; real hardware then swaps in on a proven pipeline.

## 2026-07-29 — Host strategy: Pi OS native; ROS 2 containerised; Yocto at product tier only

**Decision:** The bench stays on Raspberry Pi OS (not Ubuntu, not Yocto). ROS 2 arrives at
Phase 4 as a container (`ros:jazzy` favoured) or a Debian-built Jazzy — decided then. Yocto
enters only at the Hailo-15 port (Phase 9), prepared for now via `yocto/meta-garuda/` and the
recipe-per-dependency rule (CLAUDE.md §6 rule 6).

**Rationale:** Put the OS where the drivers are hardest, containerise what's portable. The
NPU (`hailo-h10-*` apt flow, HAT+ EEPROM detection, kernel-coupled driver packaging) and the
camera stack (libcamera pipeline handler, dtoverlays, IMX296 `trigger_mode`, RP1 PTP) are
Pi-OS-first; Ubuntu would trade vendor-path support on the schedule-critical Phase 2/3 work
for native ROS 2 — the single most portable layer in the stack. Yocto on the bench would cost
iteration speed and require hand-integrating the h10 driver with no maintained layer.
Eval-tier deliverable (Phase 8) uses pi-gen, not Yocto. Full reasoning captured from the
2026-07-29 planning discussion.

## 2026-07-29 — Base OS: Raspberry Pi OS Trixie 64-bit (not Bookworm)

**Decision:** Bench Pi runs Raspberry Pi OS Trixie (Debian 13) 64-bit Lite. CLAUDE.md §3 updated accordingly.

**Evidence:** First boot was done on Bookworm Lite (2026-06-18 oldstable image) per the original CLAUDE.md pin. `apt` on that image cannot locate `hailo-h10-all`; `apt-cache search hailo` on Bookworm shows only the Hailo-8 family (`hailo-all`, `hailort`, `hailo-dkms`, `hailofw`) — the exact packages CLAUDE.md forbids for the Hailo-10H. Direct inspection of `archive.raspberrypi.com/debian/dists/trixie/main/binary-arm64/Packages.gz` confirms the full h10 family (`hailo-h10-all`, `h10-hailort`, `h10-hailort-pcie-driver`, `python3-h10-hailort`) exists only in the Trixie pocket. Raspberry Pi OS mainline moved to Trixie 2025-10; Bookworm is the legacy/oldstable channel and will not receive AI HAT+ 2 support.

**Alternatives rejected:**
- Bookworm + vendor .debs from hailo.ai developer zone: untested kernel/driver coupling on a combo Raspberry Pi doesn't ship, login-gated downloads, manual update path. Reintroduces the #1 bring-up failure mode CLAUDE.md warns about.
- Mixing the Trixie pocket into Bookworm sources: frankendebian, ABI risk across glibc/python — rejected outright.

**Impact:** None on architecture or phase gates. Toolchain shifts to Python 3.13/GCC 14. Hailo-10H stack becomes apt-managed and kernel-coupled by the distro, which reduces version-mismatch risk.

---

## 2026-08-02 — Build ORB-SLAM3 (bench/eval) in an aarch64 GCC-11 container, not bare-metal

**Context:** The hybrid ORB-SLAM3 + Hailo-YOLO masking spike (path A of the hybrid-SLAM plan,
`docs/superpowers/plans/2026-08-02-hybrid-orbslam3-yolo-masking.md`) needs ORB-SLAM3 stereo built on
this bench. Phase-0 recon (benchlog 2026-08-02) found the host is worst-case for it: **GCC 14.2 /
Eigen 3.4 / OpenCV 4.10**, no SLAM deps present. Public ORB-SLAM3 build success is documented only up
to GCC 11–12; GCC 14 is undocumented territory (the #1 predicted build risk).

**Decision:** Build ORB-SLAM3 in a **Docker container: `ubuntu:22.04` (GCC 11, Eigen 3.4, OpenCV 4.5.4),
built natively on the Pi's arm64 CPU** — no cross-compile, no emulation. Pangolin pinned **v0.6**
(known-good with ORB-SLAM3). Headless (`bUseViewer=false`). This matches CLAUDE.md §3's committed
"heavy compute in a container, Hailo path native, talk across the boundary" architecture: the Hailo
masker stays native (HailoRT/PCIe), ORB-SLAM3 compute is containerized, they share a bind-mounted
`MaskIPC` file. Dockerfile committed at `hosts/pi5/orbslam3/`. **GPL-3.0, not-product-cleared** (rule 6,
2026-08-02 relaxation) — behind `IFrontEnd`, skeleton yocto recipe to follow, CI licence-scan visibility.

**Alternatives rejected:**
- **Bare-metal GCC 14:** the single biggest build risk, undocumented for ORB-SLAM3. No reason to fight it.
- **x86 → aarch64 cross-compile:** needs a full aarch64 sysroot for Pangolin/OpenCV/Eigen and the whole
  point (Hailo/camera) is Pi-native; large setup for no benefit on a bench spike.

**Symptom + fix logged (rule 4):** first image build **cancelled at 48%** with no compile error and no
OOM — root cause was `docker build` self-cancelling when its **launching SSH session was torn down**
(client-pipe disconnect; `nohup` blocks SIGHUP but not this). GCC 11 was compiling the Eigen-heavy files
(`Optimizer.cc`, `G2oTypes.cc`, `ImuTypes.cc`, `CameraModels/*`) cleanly — the container pivot works.
Fix: relaunch the build under **`setsid`** (own session, immune to SSH teardown). Layer cache (apt +
Pangolin, ~1.2 GB) made the relaunch resume cheaply.

**CORRECTION — the real root cause of the repeated ~48% deaths was NOT client-disconnect or the file
itself, it was a broken parallelism `sed`.** Upstream `build.sh` uses bare `make -j` (UNLIMITED jobs)
for Thirdparty and `make -j4` for the main lib. `sed 's/make -j/make -jN/'` mangles `make -j4` →
`make -jN4` (e.g. `make -j24` at "-j2", `make -j14` at "-j1"), so the main build ran 14–24-way parallel;
many concurrent cc1plus incl. `Optimizer.cc` (~7 GB alone at -O3) drove load to 18+ and OOM-killed the
build — silently (SIGKILL), no error text, host RAM freed on death (looked like "not OOM"). Zombie
BuildKit daemon workers from repeated relaunches compounded it. **Real fix: anchored regex
`sed -E 's/make -j[0-9]*/make -j2/g'` pins EVERY make line to -j2, plus a 6 GB temp swapfile for
`Optimizer.cc`'s ~7 GB peak (CLAUDE.md §3 sanctions swap for on-device builds).** With that, one clean
`-j2` build completed: load ~2, min-avail 2.2 GB, `Optimizer.cc` cleared, image `garuda/orbslam3:bench`
(3.29 GB), never throttled during the build (peak 78 °C).

**Go/no-go verdict (bench/benchlog.md 2026-08-02): PASS.** ORB-SLAM3 stereo on EuRoC MH_01 = **~22.8 fps
tracking (43.85 ms/frame mean, 41.96 ms median), clearing the P4 ≥20 Hz front-end bar even while thermally
throttled** (peak 84.5 °C, freq-capped; passive heatsink — sustained runs throttle, no under-voltage).
Better than the research forecast. The hybrid ORB-SLAM3 + Hailo-YOLO masking is viable → proceed to the
mask seam (Task 3). Caveats (not blockers): tracking-thread rate only (no ≥200 Hz inertial pose — stereo-only,
no IMU); EuRoC-ideal (live OV9281 `TBD-bench`); ATE accuracy not yet computed (`TBD-bench`).

---

## 2026-08-03 — OV9281 CamArray is not a stereo-SLAM camera; IMX296 GS pair is the VIO path  [⚠️ RETRACTED 2026-08-04 — see below]

**Context:** Attempted live stereo SLAM (ORB-SLAM3) on the Arducam **Camarray HAT + OV9281 quad** (tiles
0+1 as the pair). Full tooling built and works (`camarray_record.py`, `orbslam3_config.py`,
`rectify_clip.py`), but ORB-SLAM3 never tracks — only ~25 stereo map points/frame.

**Root cause (exhaustively pinned — bench/benchlog.md 2026-08-03):** instrumented `Frame::
ComputeStereoMatches` — rectification is FINE (right kp on the same rows, dy≈0), but the two OV9281
images don't **correspond**: ORB descriptor match is near-random (median best-Hamming ≈100/256; descPass
≈25/1200), SGBM density only ~29%. Ruled out, one by one: scene, L/R order, config, vertical alignment,
**focus** (owner refocused both lenses sharp+matched, 118/99→~600/580 — no change), **photometry**
(CLAHE/histmatch — no help), **noise** (Gaussian/NLmeans denoise — no help). It is fundamental to the
hardware: two cheap, subtly-mismatched OV9281 optics/sensors.

**Decision:** The OV9281 CamArray is **confirmed not VIO-grade** — matches CLAUDE.md §2 ("inference/
tracking/VLM dev only"; the **IMX296 GS pair = "the ONLY VIO-grade cameras"**). All SLAM software is
proven on EuRoC (22.8 fps, ATE 4.66 cm) and is drop-in ready for the IMX296.

**IMX296 wiring — plan of record:** two IMX296 on the **Pi 5's two native CSI ports** (`dtoverlay=imx296,
cam0` / `,cam1`) + **XTR hardware trigger** (1.8 V; R11 rework) — full per-camera CSI bandwidth, mainline
`imx296` driver, no multiplexer. **Camarray-HAT alternative:** the Camarray platform *does* list IMX296
support, so the existing HAT *could* run IMX296 — BUT requires Arducam's Camarray-specific IMX296 modules
(standard modules won't work) + a **firmware reflash** (per-sensor) + the stereo (not quad) config; a
ready IMX296-stereo Camarray kit was not confirmed (OV9281/IMX477/IMX708 stereo kits exist). Native-CSI
path is cleaner and preferred. Verify against the HAT SKU before buying. (Sources: Arducam Camarray docs.)

**Silver lining:** the owner's refocus (sharp + matched) improves the OV9281 for its *actual* jobs
(depth/tracking/inference), which don't need cross-camera correspondence.

## 2026-08-04 — RETRACTION: OV9281 CAN do stereo SLAM; the failure was calibration, not the sensor

**Supersedes the 2026-08-03 decision above.** Owner challenged "IMX296 won't fix it — it's the
implementation," and a controlled re-investigation proved them right. Full write-up:
`docs/2026-08-slam-ov9281-progress.md` (Part 3); scripts in `bench/ov9281-stereo/`; numbers in
`bench/benchlog.md` (2026-08-03/04 entries).

**What was actually true:**
- The OV9281 pair is **valid stereo** — free descriptor matching gives median Hamming ~26 and **80% of
  matches fit one fundamental matrix**. The old "near-random descriptors" reading was measured *under* a
  broken rectification (the `ComputeStereoMatches` same-row search), so it compared wrong points.
- The stored stereo **calibration is broken** — it doesn't even rectify its own checkerboard captures
  (126 px corner error), and the checkerboard set's L/R correspondences are inconsistent (unrecoverable).
- **Scene self-calibration must be fit from STILL frames.** Fitting from a moving clip inflates the
  recovered inter-camera rotation by ego-motion (true ~2.4° → bogus 7.5°) and wrecks rectification.
- **Controlled still-then-move test:** with a still-fit calibration, rectification is 0.00 px static and
  ≤ 2 px under fast motion, and **ORB-SLAM3 tracks the clip with 2/207 dropped frames (~1%)** — clean.
- A **small real desync** (~10–16 ms L/R offset, `dy ≈ 0.108 × motion`) adds ≤ 2 px under motion — within
  ORB-SLAM3's tolerance. A precision refinement, not a blocker.

**Decision:**
- **OV9281 stereo SLAM is viable** with a correct calibration. It is no longer "not a stereo-SLAM camera."
- The **IMX296 GS pair remains the product path**, but for the *correct* reason — capture **simultaneity**
  (hardware trigger), worth ≤ 2 px — not "cheap/mismatched optics."
- **Action:** recalibrate the OV9281 pair on a **rigid mount** (current rig is cardboard + tape, hand-moved)
  from still frames or a fresh, well-diversified checkerboard shoot; verify |dy| ≈ 0 before trusting a run.
- The 2026-08-03 "hardware wall" reasoning (and the `dafdb70`/`4d59735`/`1326a4a` conclusions) are wrong.
  This is a rule-9 failure caught late: an assumed-correct component (rectification) was the actual bug.

## 2026-08-04 — Stereo target-range: NPU multi-tenancy spike → Option B (single-process stereo)

**Context:** S2 v1 (metric distance of the tracked object) originally designed as CM3 track + OV9281
range — two processes, both running yolov8m, needing concurrent NPU. Task-1 spike (garuda-sdk plan
`docs/superpowers/plans/2026-08-04-stereo-target-range.md`) tested that assumption on the 10H.

**Findings (measured on this Pi, HailoRT 5.1.1 / h10-hailort):**
- yolov8m HEF = `/usr/share/hailo-models/yolov8m_h10.hef` (same as garuda-inference). On-chip NMS-by-class;
  `bindings.output(name).get_buffer()` returns a **per-class list of arrays** already (easy decode). Solo
  infer **36.2 fps**.
- **Two separate processes cannot share the Hailo by default:** the 2nd gets
  `HAILO_OUT_OF_PHYSICAL_DEVICES(74)` — a plain `VDevice` seizes the NPU exclusively.
- The HailoRT **multi-process service daemon is NOT packaged for this stack**: no systemd unit, no
  `hailort_service` binary, not in apt. Cross-process sharing would require source-building/installing it
  against the pinned 5.1.1 Hailo stack — high risk per rule 11. **Not done.**
- **Intra-process** sharing is free (one process, one yolov8m fed both streams — 30 fps CM3 + 6 fps stereo
  ≪ 76 fps capacity).

**Decision (owner: user, 2026-08-04):** Ship **Option B** — do detection + single-target lock + ranging
**entirely in garuda-depth on the OV9281 stereo pair** (one process, one yolov8m, no cross-process NPU
conflict, no system change). The CM3 is not run concurrently for this use case; the mono size-estimate
fallback is dropped (distance is stereo, or "no range"). True cross-process concurrency (CM3 colour
tracking + stereo ranging together) is deferred to **P6**, where the concurrent-NPU work — and, if needed,
building the HailoRT multi-process service — is already scoped.

**Licence note:** yolov8m (Ultralytics) is **AGPL-3.0** — the same detector already used on the bench
(P1). It is bench/eval, **not product-cleared**; the ranging code itself (`target_range`, `roi_disparity`)
is Apache/clean. Product tier needs a permissively-licensed detector (yolox/yolov6). Tag the yolov8m use
`not-product-cleared`.

## 2026-08-07 — Platform migration to HailoRT 5.3.0 + YOLO26 (fresh SD, single color camera)

**Why:** 5.1.1 constraints (grayscale-stereo flicker, yolov11m missing small objects like phones,
relative-only depth) kept blocking the single-camera perception goal. Owner directed the upgrade.

**Key facts (all verified):**
- The Pi's `h10-*` Hailo stack (Raspberry Pi apt repo) is pinned at **5.1.1**; no public 5.4.0 for ARM.
  Hailo Dev Zone has **HailoRT 5.3.0 arm64** (`hailort_5.3.0_arm64.deb`, cp313 aarch64 whl,
  `hailort-pcie-driver_5.3.0_all.deb`) — 5.3.0 is the newest ARM runtime; **firmware is bundled in the
  pcie-driver deb** (/lib/firmware/hailo/hailo10h/, auto-flashed by the driver at boot — no fw-update).
- YOLO26 + DepthAnything + yolov11m all have **v5.3.0 compiled HEFs** on the public model-zoo S3
  (`.../Compiled/v5.3.0/hailo10h/`). So 5.3.0 runtime + v5.3.0 HEFs is a matched set.
- **CRITICAL kernel patch:** the 5.3.0 pcie-driver source uses `del_timer_sync()`, REMOVED from the
  kernel (>=6.15) in favour of `timer_delete_sync()`. Against rpi kernel 6.18.34 the dkms build fails.
  Fix: `sed 's/del_timer_sync/timer_delete_sync/;s/\bdel_timer\b/timer_delete/'` on
  `linux/vdma/monitor.c` (2 occurrences), then `dkms build/install`. **A re-flash MUST re-apply this.**
- Done on a **spare SD (fresh Raspberry Pi OS Trixie 64-bit)** — the working 5.1.1 card is preserved.
- **YOLO26 HEF is NMS-free/DFL-free:** 6 raw tensors (3 box ltrb-heads H*W*4 + 3 class-logit heads
  H*W*80 @ strides 8/16/32). Decode = box*stride anchor-free + sigmoid(class) + light NMS
  (`hosts/pi5/hailo_yolo26.py`). Validated: suitcase @0.92, person @0.93, boxes exact.

**Result:** new bench = Trixie / kernel 6.18.34 / HailoRT+FW+driver **5.3.0** / Python 3.13.5.
Single-camera perception `hosts/pi5/garuda_mono.py` (systemd `garuda-mono`, :8098): CM3 (rpicam-vid)
-> YOLO26 detect + DepthAnything relative depth -> live-calibrated metric distance, self-served GUI.
Numbers: yolo26m **58 fps** (hailortcli run2), mono pipeline **25 fps**. Licence: YOLO26/yolov11m
Ultralytics AGPL (not-product-cleared); DepthAnything V2 small Apache-2.0.

## 2026-08-07 — Metric depth: DepthAnything V2 (relative) -> YOLO26-depth (metric) in garuda-mono

Why: DepthAnything V2 ViT-S outputs RELATIVE depth — needed 2+ manual cal points per scene and
still jittered (~±18 % raw on a locked static-ish target). YOLO26-depth predicts metres directly
(unbounded log-depth head, ~0.02–150 m) and measured 7.7x faster than DA-V2-S per Ultralytics.

How the HEF is built (full procedure in `models/build_yolo26_depth_hef.sh`):
Ultralytics `yolo export model=yolo26n-depth.pt format=hailo name=hailo10h` on x86 with the DFC
5.3.0 wheel extracted from the Hailo AI SW Suite 2026-04 `.run` (`--nox11 --noexec --keep`).
The HEF is cut at the depth head's final logit conv; host decode = exp(clip(logit,-4,5))**cal_a
* exp(cal_b) with cal_a/cal_b from the exporter's metadata.yaml (`hosts/pi5/hailo_yolo26_depth.py`).

Findings (x86, 14 GB RAM):
- The exporter enables DFC QAT finetune + optimization_level=2 unconditionally -> OOM-killed twice
  (~6.6 GB anon RSS; TF CPU training). Fixes applied IN THE VENV exporter: finetune policy=disabled,
  optimization_level=1. Layer Noise Analysis is invoked UNCONDITIONALLY in the DFC's
  `optimization_flow.finalize_optimization()` (no level/policy gate) — it survived at level 1 on
  this box but is the next thing to stub if a bigger model OOMs.
- garuda-mono `_est()` now treats raw depth as metres (identity); the old relative->metric cal fit
  remains as an optional scale refinement. `_sample()` made map-size-agnostic (DA=224, Y26D=192).
- DepthAnything class + HEF kept on the Pi as a fallback path.

Licence: Ultralytics YOLO26(-depth) AGPL-3.0 — bench/eval, not-product-cleared (owner relaxation
2026-08-02). Product tier still needs a licence-clean depth model before Phase 8.

## 2026-08-07 (evening) — Production y26d HEF: GPU QAT infeasible on 6 GB → PTQ-only at level 2

- GPU restored after reboot (driver 580.173.02). TF 2.19 then saw the GPU but the hailo-export venv
  had no ptxas/libdevice (torch's nvidia wheels don't include nvcc) → every GPU op failed with
  "JIT compilation failed [Op:Log]". Fix: `uv pip install nvidia-cuda-nvcc-cu12==12.8.93` into the
  venv. (Never surfaced before: the broken driver hid the GPU and everything silently ran on CPU.)
- QAT finetune (distillation: float + quantized nets co-resident at 768²) OOMs the 6 GB RTX 3060 at
  batch 8, 4 AND 2 (even with TF_FORCE_GPU_ALLOW_GROWTH=true); observed 323 s for step 1/512 → even
  a fitting batch-1 run projects to a day+. DECISION (owner): PTQ-only — finetune policy=disabled,
  optimization_level=2 kept, 1024 calib images (256 CM3 + 768 SUN RGB-D val; `depth-calib.yaml`).
  CM3 frames carry placeholder depth npys — the loader needs them to exist, calibration reads images
  only. Venv exporter patch state now: level 2 + finetune disabled (no batch_size arg).
- Layer Noise Analysis is still unconditional; at level 2 it OOMs the GPU but degrades to a WARNING
  (skips stats) and the flow completes — no stub needed.
- Survey (owner asked): NO pre-compiled metric indoor mono depth model exists for hailo10h — model
  zoo carries fast_depth/scdepthv3 (relative) + stereonet (collapses indoors); hailo-apps wraps the
  same scdepthv3 and its new YOLO26 HEFs are detect/pose only. Locally-compiled yolo26m-depth
  remains the only metric option.
- Probe gate PASS on deploy (logit std 0.263 vs ~0.04 collapse) — benchlog. Move-test + tape next.
