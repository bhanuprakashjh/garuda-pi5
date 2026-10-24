# Garuda Pi 5 bench — User Guide

A complete guide for bringing up and using the Garuda SDK bench tier: a Raspberry Pi 5
with a Hailo-10H AI accelerator running live perception services. It assumes you have the
hardware in hand and **no prior project context**.

Conventions used throughout:

- The Pi's bench user is `garuda`; the Pi sits at `10.42.0.128` on the direct-Ethernet
  link (your PC is `10.42.0.1`), and at `10.43.0.1` when acting as a WiFi access point.
- Every performance number quoted here is copied from [`../bench/benchlog.md`](../bench/benchlog.md)
  with its measurement date. Anything not yet measured is written **`TBD-bench`** — never
  trust a number for this hardware that does not trace to the bench log.

---

## Table of contents

1. [Overview & architecture](#1-overview--architecture)
2. [Hardware assembly](#2-hardware-assembly)
3. [OS install](#3-os-install)
4. [Hailo-10H setup](#4-hailo-10h-setup)
5. [Deploying the services](#5-deploying-the-services)
6. [Using the garuda-mono GUI](#6-using-the-garuda-mono-gui)
7. [The portal](#7-the-portal)
8. [Networking](#8-networking)
9. [Benchmarks & phase gates](#9-benchmarks--phase-gates)
10. [Troubleshooting](#10-troubleshooting)
11. [Licence](#11-licence)

---

## 1. Overview & architecture

**Garuda** is a companion-computer autonomy stack for drones and robotics. This repo is
its **bench/dev tier**: the Raspberry Pi 5 + Hailo-10H combination used to develop and
measure host-agnostic services (capture, inference, tracking, depth, VIO, time sync,
bridge) before they port to a Hailo-15 SoM at product tier.

What runs where:

```
                 your PC (10.42.0.1)                Raspberry Pi 5 (10.42.0.128)
  browser ──────────────────────────────►  garuda-portal  (:8080, web configurator)
  browser ──────────────────────────────►  garuda-mono    (:8098, perception + debug GUI)
                                                │
                                                ├── Camera Module 3 (CSI)
                                                └── Hailo-10H NPU (PCIe, AI HAT+ 2)

  flight controller (PX4 on a PIC32CZ CA90) — a SEPARATE device over Ethernet,
  not part of this bench.
```

Architecture rules that shape everything here:

- **The inner control loop never crosses Ethernet.** The companion computer publishes
  odometry and obstacles; the flight controller owns attitude/rate control.
- **Failsafe gates on odometry covariance/validity, never on link liveness** — a frozen
  but confident pose stream is worse than a dead one (demonstrated in SITL; see
  `bench/benchlog.md` 2026-07-30).
- **One PTP clock domain** for camera triggers, image timestamps, and IMU data.
- **Every sensor sits behind a plugin interface** (`ICaptureBackend`, `IFrontEnd`,
  `IDepthSource` in `interfaces/`) — no Pi-specific calls outside `hosts/pi5/`.

> **Safety: nothing on this bench arms motors.** There is no flight hardware here. Any
> code that could reach PX4 (DDS/MAVLink) defaults to a simulated endpoint and is gated
> behind `GARUDA_SIM=1` or an explicit bench flag. The portal deliberately exposes no
> arming or offboard command.

The two services you will interact with daily:

- **garuda-mono** (`:8098`) — the perception service. It owns the Camera Module 3 and
  runs, on the NPU: YOLO26m detection on every frame, ByteTrack multi-object tracking
  with click-to-lock, and a monocular depth model sampled over the locked target's box to
  produce a live metric distance. It also offers an open-vocabulary detector mode
  (text prompts) and a pose/gesture mode.
- **garuda-portal** (`:8080`) — the browser configurator: system health, camera view,
  the converged Tracking view (fed by mono's event stream), a calibration wizard, a
  readiness go/no-go card, settings and logs.

---

## 2. Hardware assembly

### Parts list

| # | Part | Role |
|---|---|---|
| 1 | Raspberry Pi 5, 8 GB | Host computer |
| 2 | AI HAT+ 2 (Hailo-10H, 40 TOPS, 8 GB on-module DRAM) | NPU, on PCIe |
| 3 | 30 mm PCIe FFC + 16 mm stacking header | Mounts the HAT |
| 4 | Raspberry Pi Camera Module 3 (IMX708, rolling shutter) | CSI camera for detection/tracking/depth |
| 5 | Official Raspberry Pi **27 W (5 V ⎓ 5 A)** USB-C PD supply | Power — see caution below |
| 6 | 256 GB microSD | Boot + OS |
| 7 | Passive heatsink | Cooling — see caution below |
| 8 | Ethernet cable to your PC | Bench link |

Optional / later-phase hardware (present in the codebase but not required for the core
demo): 2× IMX296 global-shutter cameras (the only VIO-grade cameras), an Arducam OV9281
CamArray HAT (stereo-depth experiments, drives `garuda-depth`), an Arducam ToF camera.

### Assembly order

1. With the Pi **unpowered and unplugged**, mount the AI HAT+ 2 on the stacking header
   and connect the 30 mm PCIe FFC. Seat the FFC firmly — a half-seated FFC is the single
   most common cause of the Hailo device not enumerating.
2. Attach the Camera Module 3 to the **CAM/DISP 0** CSI connector (see cautions).
3. Insert the flashed microSD (next chapter), connect Ethernet, then power.

> **⚠️ CSI is NOT hot-pluggable.** Attach or detach the camera **only with the Pi fully
> powered off and unplugged**. Hot-plugging a CSI camera can destroy the sensor and/or
> the Pi's CSI interface.

> **⚠️ Camera cable orientation.** At the **Pi end**, the gold contacts face **toward the
> Ethernet jack**. At the **camera end**, the gold contacts face **toward the camera
> PCB**. A reversed or half-seated cable produces the classic "no cameras available"
> symptom diagnosed in [§10](#camera-not-detected--full-diagnostic-procedure) — this
> exact failure occurred on this bench (benchlog 2026-07-29).

> **⚠️ Passive cooling limits.** The bench uses a passive heatsink with no fan. Under
> sustained one-core CV/NPU load the SoC plateaus around **~84 °C and thermally
> throttles**. Check after any load test:
>
> ```bash
> vcgencmd get_throttled    # 0x0 = OK; anything else -> log it, distrust perf numbers
> ```
>
> Keep on-device tools light (cap fps, throttle loops), and never trust a benchmark taken
> while any throttle bit was set.

> **⚠️ Verify a genuine 5 V / 5 A supply.** A "60 W" GaN charger is not enough: on this
> bench one negotiated only **3 A on its 5 V leg** (15 W ceiling) and caused repeated
> brownouts and hard reboots under NPU+camera load (measured 2026-08-09, root-caused in
> `docs/decisions.md`). The official 27 W supply was verified on-bench 2026-08-10:
> negotiated contract 5000 mA, `get_throttled` 0x0 with the perception service hot. Check
> yours:
>
> ```bash
> xxd /proc/device-tree/chosen/power/max_current   # expect 5000 mA
> vcgencmd pmic_read_adc EXT5V_V                   # 5 V rail under load
> ```
>
> Do **not** apply the EEPROM `PSU_MAX_CURRENT=5000` workaround on a 3 A supply — it
> suppresses the warning flag while deepening the rail sag. The flag is the diagnostic,
> not the problem.

> **⚠️ If you later add IMX296 global-shutter cameras:** their XTR external-trigger pad is
> **1.8 V logic — driving it with 3.3 V destroys the sensor**. Never wire a GPIO toward
> XTR without an explicit 1.8 V level-shift path (and the R11 rework the module needs for
> external trigger).

---

## 3. OS install

The OS is **Raspberry Pi OS Trixie 64-bit** (Debian 13), Lite. Trixie is mandatory: the
Hailo-10H `h10-*` packages exist **only** in the Trixie apt repo — Bookworm carries only
the Hailo-8 line (verified against the repo indexes; `docs/decisions.md` 2026-07-29).

1. On your PC, flash `raspios-trixie-arm64-lite` to the microSD with Raspberry Pi Imager
   (or `dd`), verifying the image checksum first.
2. Before first boot, seed the boot partition (`bootfs`) with:
   - an empty file named `ssh` (enables SSH), and
   - `userconf.txt` containing `garuda:<encrypted password>` (create the hash with
     `openssl passwd -6`). This creates the `garuda` user at first boot.
3. Boot, connect over the direct-Ethernet link ([§8](#8-networking)), and record the
   baseline before installing anything:

   ```bash
   uname -a
   cat /etc/os-release
   vcgencmd get_throttled
   lspci
   ```

   Append a dated snapshot to `bench/versions.md`. **Hailo driver/kernel coupling is the
   #1 bring-up failure** — a versions snapshot per change is what makes regressions
   diagnosable.

Rules that keep the bench stable:

- **OS major upgrades are always a fresh flash, never in-place** (in-place major upgrades
  are unsupported on Raspberry Pi OS).
- **Never run `apt upgrade` casually.** A kernel bump can break the Hailo driver
  mid-sprint. Upgrades are deliberate: snapshot `versions.md`, upgrade, re-run the
  [§4 sanity checks](#sanity-checks), record the result.

---

## 4. Hailo-10H setup

### Install

```bash
sudo apt update
sudo apt install hailo-h10-all
```

> **⚠️ The package-family trap.** The Hailo-10H uses the **`h10-*` family**
> (`hailo-h10-all`, `h10-hailort`, `h10-hailort-pcie-driver`, `python3-h10-hailort`).
> **Never install plain `hailort` / `hailo-all` / `hailo-dkms`** — that is the Hailo-8
> line and will not drive this device. If both families ever appear installed, purge both
> and reinstall h10 only.

`hosts/pi5/install.sh` does this install for you (plus the service deploy) — see [§5](#5-deploying-the-services).

### Sanity checks

```bash
hailortcli fw-control identify   # MUST report Device Architecture: HAILO10H
lspci | grep -i hailo            # MUST show the Hailo device
vcgencmd get_throttled           # 0x0
```

Interpretation:

| Symptom | Meaning |
|---|---|
| Device in `lspci` but `hailortcli` fails | Driver/runtime **version mismatch** — check that kernel, pcie driver and HailoRT come from one matched set |
| Device absent from `lspci` | **Reseat the PCIe FFC** (power off first) |
| Identify reports something other than HAILO10H | Wrong package family installed — purge and reinstall `h10-*` |

### The HailoRT 5.3.0 stack (what this snapshot targets)

The Trixie apt repo pins the h10 family at 5.1.1. This branch runs **HailoRT 5.3.0**,
installed from the Hailo Developer Zone (`hailort_5.3.0_arm64.deb`, the cp313 aarch64
Python wheel, and `hailort-pcie-driver_5.3.0_all.deb` — the firmware is bundled in the
pcie-driver deb and auto-flashed at boot). All deployed HEFs are the matching v5.3.0
compiles. Details: `docs/decisions.md` 2026-08-07.

> **⚠️ Kernel ≥ 6.15 build patch (required).** The 5.3.0 pcie-driver source still calls
> `del_timer_sync()`, which was removed from the kernel (≥ 6.15) in favour of
> `timer_delete_sync()`. On the bench kernel (6.18.34) the dkms build fails until you
> patch the driver source — two occurrences in `linux/vdma/monitor.c` inside the driver's
> dkms source tree (under `/usr/src/`):
>
> ```bash
> sudo sed -i 's/del_timer_sync/timer_delete_sync/g; s/\bdel_timer\b/timer_delete/g' \
>     <driver-src>/linux/vdma/monitor.c
> sudo dkms build  <module>/<version>   # then: sudo dkms install <module>/<version>
> ```
>
> **A re-flash of the SD card must re-apply this patch** (`docs/decisions.md` 2026-08-07).

Python note: the system Python is 3.13; the Hailo bindings ship for it. Use venvs with
`--system-site-packages` so the bindings stay visible. If pyHailoRT misbehaves, verify
the binding was built for the running Python **before** debugging anything else.

### Known pitfalls — apply ONLY on symptom, never pre-emptively

These fixes are deliberately not applied up front (project rule 4): apply one only when
you reproduce its symptom, and log symptom + fix in `docs/decisions.md`.

| Symptom | Fix |
|---|---|
| vDMA timeout (error −110) | Add `pcie_aspm=off` to `/boot/firmware/cmdline.txt`; set `kernel=kernel8.img` in `config.txt`; add `options hailo1x_pci force_desc_page_size=4096` to `/etc/modprobe.d/hailo_pci.conf` |
| PCIe Gen3 instability | Fall back to Gen2: `dtparam=pciex1_gen=2` in `config.txt`. Acceptable — the 10H holds weights in on-module DRAM, so link bandwidth is not critical. Note the fallback in the bench log |

---

## 5. Deploying the services

### What lives where on the Pi

| Location | Contents |
|---|---|
| `/home/garuda/garuda-sdk-deploy/` | The rsynced repo (deploy source). **garuda-mono runs from here**: `hosts/pi5/garuda_mono.py`, model HEFs under `models/`, calibration file `hosts/pi5/mono_cal.json` |
| `/opt/garuda/garuda-portal/` | Deployed portal code (runs as `User=garuda`) |
| `/opt/garuda/garuda-configd/` | Deployed configd code (root-owned, root-run) |
| `/opt/garuda/garuda-inference/`, `/opt/garuda/garuda-follow/`, `/opt/garuda/interfaces/` | Supporting deployed trees |
| `/etc/garuda/config.json` | Config store, written atomically by configd |
| `/etc/systemd/system/garuda-*.service` | Units |

### Install / redeploy

1. From the PC, stage the repo onto the Pi:

   ```bash
   rsync -a --exclude .git ./ garuda@10.42.0.128:~/garuda-sdk-deploy/
   ```

2. On the Pi, run the idempotent provisioning script (safe to re-run; it checks before
   modifying anything):

   ```bash
   cd ~/garuda-sdk-deploy && ./hosts/pi5/install.sh
   ```

   It: verifies the OS is Trixie, grants the bench user passwordless sudo (bench posture —
   see the security note in [§7](#security-posture)), installs `hailo-h10-all` + `ethtool` +
   `python3-opencv`, installs the Hailo GenAI model zoo deb (provides `hailo-ollama`),
   deploys the portal/configd/depth services to `/opt/garuda/`, installs and restarts
   their systemd units, and finishes with the identify + throttle sanity checks.

3. To push a code change to an already-provisioned bench without the apt round-trip:

   ```bash
   ./hosts/pi5/install.sh --deploy-only
   ```

### Models

HEF binaries are mostly git-ignored — fetch or build them into
`~/garuda-sdk-deploy/models/` on the Pi:

- `models/fetch.sh` pulls the pre-compiled Hailo model-zoo HEFs (fast_depth, stereonet,
  scdepthv3, yolo_world_v2s, yolov8m_pose) and the CPU-side CLIP text encoder. Each is
  tagged with its licence; see `models/LICENCES.md`.
- `yolo26m.hef` and `depth_anything_v2_vits.hef` come from the Hailo model-zoo S3
  (`Compiled/v5.3.0/hailo10h/`) — the runtime and HEF versions must match.
- `yolo26m-depth.hef` (the metric depth model) is **built on an x86 PC**, never on the
  Pi: `models/build_yolo26_depth_hef.sh` documents the Dataflow Compiler procedure.

### The garuda-mono unit

The unit shipped at `hosts/pi5/units/garuda-mono.service` is a verbatim copy of the one
running on the bench (captured 2026-10-24). Install it as
`/etc/systemd/system/garuda-mono.service`:

```ini
[Unit]
Description=Garuda Mono - single CM3 YOLO26 + DepthAnything (:8098)
After=network.target
[Service]
ExecStart=/usr/bin/python3 -u /home/garuda/garuda-sdk-deploy/hosts/pi5/garuda_mono.py
Restart=on-failure
User=garuda
[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now garuda-mono
```

### The `GARUDA_MONO_DEPTH` drop-in (boot-time depth source)

The depth engine garuda-mono boots with is set by an environment variable in a systemd
drop-in (shipped at `hosts/pi5/units/garuda-mono.service.d/depth.conf`, a verbatim copy
of the bench's):

```ini
# /etc/systemd/system/garuda-mono.service.d/depth.conf
[Service]
Environment=GARUDA_MONO_DEPTH=y26d
```

| Value | Engine |
|---|---|
| `y26d` | **yolo26m-depth HEF** — metric monocular depth (metres directly; calibration optional, as a scale refinement). The deployed default. |
| `da` | **DepthAnything V2 (small)** — relative depth; **requires 2+ calibration points** before a metric estimate appears. Kept as the fallback path. |

After editing a drop-in:

```bash
sudo systemctl daemon-reload && sudo systemctl restart garuda-mono
```

Both engines also stay live-switchable at runtime without a restart
(`POST /api/depth_engine`, [§6](#6-using-the-garuda-mono-gui)) — the drop-in sets only
the boot default.

Other garuda-mono environment knobs (same drop-in mechanism; defaults in parentheses):
`GARUDA_MONO_YOLO` (detector HEF path), `GARUDA_MONO_POSE` (pose HEF path),
`GARUDA_MONO_DEPTH_EVERY` (3 — depth every Nth frame), `GARUDA_MONO_CONF` (0.15),
`GARUDA_MONO_MAXLOST` (25 — frames a lost track coasts), `GARUDA_MONO_REACQ` (5 — coasted
frames before jumping to a live same-class track), `GARUDA_MONO_EMA` (0.3 — distance
smoothing), `GARUDA_MONO_WORLD_CONF` (0.10).

### Service management

```bash
systemctl status garuda-mono garuda-portal garuda-configd garuda-depth
sudo systemctl restart garuda-mono            # reload code after a redeploy
journalctl -u garuda-mono -f                  # live logs
journalctl -u garuda-portal --since -1h
```

Note: after redeploying code, always `restart` (not `enable --now`) — an already-active
unit keeps running the old process otherwise (bench finding, 2026-07-31).

Camera ownership: the CM3 is a single-client device and **garuda-mono is its sole
holder** in the converged topology. The portal acquires the camera lazily (only while its
own Camera tab is actually viewed) and releases it on disconnect. `garuda-depth` serves
the optional OV9281 CamArray HAT, not the CM3, and idles with no viewer.

---

## 6. Using the garuda-mono GUI

Open `http://10.42.0.128:8098/` (or `http://10.43.0.1:8098/` over the WiFi AP).

The page shows the live camera with detection overlays, a distance readout, a 60-second
distance chart, and calibration controls. Compute runs **only while someone is watching**
(a stream viewer or an SSE subscriber) — with no viewer the service idles.

### Click-to-lock tracking

- All detections are drawn as faint grey boxes; the **locked target** is a bold green box.
- **Click any detected object in the video to lock it.** The click picks the track under
  the cursor (smallest box wins), or the nearest track. The class dropdown also works:
  pick a class and mono locks the best live track of that class.
- **Coasting:** if the detector misses the target, the lock coasts on the tracker's
  prediction for up to 25 frames — the box dims (darker green, `(coast)` tag in the
  banner) while coasting.
- **Re-acquire:** after 5 coasted frames, if a live detection of the wanted class exists
  elsewhere in frame, the lock jumps to it (handles targets that move too fast for IoU
  association). Losing the target entirely returns to `searching...`.

### Distance and the chart

The depth engine runs every 3rd frame; depth is sampled as the median over the locked
target's box and EMA-smoothed:

- **raw** — the engine's own reading (metres with `y26d`; unitless with `da`).
- **est** — the calibrated estimate in metres, plotted on the 60 s history chart.
- The banner over the video states exactly what you're seeing: `searching...`,
  `locked (depth pending)`, `raw N (add 2+ cal points)` (relative engine, uncalibrated),
  or `X.XX m`.

> The y26d engine's metric scale passed its deployment probe gate (2026-08-07) but its
> physical accuracy against a tape measure is **TBD-bench** — treat absolute distances as
> unvalidated until that run lands in the bench log.

### Depth calibration workflow

Calibration maps raw readings to true distance (required for `da`, an optional scale
refinement for `y26d`):

1. Lock the target (click it).
2. Measure the true camera→target distance with a tape measure.
3. Enter it in **True distance (m)** and press **Add cal point**.
4. Repeat at a different distance — **2+ points** are needed before a fit appears. Mono
   fits both a linear and an inverse model and keeps whichever fits better (the badge
   shows which).
5. **Reset cal** clears all points.

Cal points **persist across restarts** in
`~/garuda-sdk-deploy/hosts/pi5/mono_cal.json` on the Pi. Reset the calibration whenever
you change the depth model or HEF — old points are meaningless against a new engine.

### Depth-engine switching

- Boot default: the `depth.conf` drop-in ([§5](#the-garuda_mono_depth-drop-in-boot-time-depth-source)).
- Live switch (no restart; both engines stay configured on the NPU):

```bash
curl -X POST 'http://10.42.0.128:8098/api/depth_engine?engine=da'
curl -X POST 'http://10.42.0.128:8098/api/depth_engine?engine=y26d'
```

A switch drops the stale depth map and smoothing state; the portal's Tracking tab has a
toggle for the same call.

### Detector modes

```bash
# default COCO-80 YOLO26m
curl -X POST 'http://10.42.0.128:8098/api/detector?mode=primary'

# open-vocabulary: yolo_world_v2s + CLIP text prompts (up to 80)
curl -X POST 'http://10.42.0.128:8098/api/detector?mode=world&prompt=pen&prompt=keys'

# pose: yolov8m_pose, 17 COCO keypoints + gesture recognition
curl -X POST 'http://10.42.0.128:8098/api/detector?mode=pose'
```

Mode switches reset the tracker (the label space changed) and revert to `primary` on
service restart. A failing world/pose path auto-reverts to primary after 10 consecutive
inference errors rather than stranding the view. The first world switch is slow (~4.5 s
measured 2026-08-07: lazy HEF configure + CLIP session + prompt encode); re-prompting an
already-configured world detector takes under a second.

Pose mode feeds the gesture state machine used by the portal: **STOP** pauses follow,
**FOLLOW ME** re-locks to the gesturing person, **POINT** is HUD-only. Operating
envelope: the subject's full upper body (head→hips) must be in frame; verified live at
2 m with zero false activations over ~10 min (2026-08-10).

### API reference (mono, `:8098`)

| Method & path | Purpose |
|---|---|
| `GET /` | The debug GUI |
| `GET /stream` | MJPEG annotated video (holding it open counts as a viewer) |
| `GET /api/state` | One JSON state snapshot: tracks, lock, distance, engine, detector, cal, gesture |
| `GET /api/events` | Server-Sent Events — the same snapshot per processed frame, throttled to ≤10 Hz |
| `GET /api/mono_range` | Distance/calibration readout (polled by the GUI) |
| `POST /api/mono_click?x=&y=` | Lock the track under/nearest a click (native pixels) |
| `POST /api/mono_target?class=` | Lock-by-class |
| `POST /api/mono_calibrate?dist=` | Add a cal point at the current raw reading |
| `POST /api/mono_calibrate_reset` | Clear calibration |
| `POST /api/depth_engine?engine=y26d\|da` | Live depth-engine switch |
| `POST /api/detector?mode=primary\|world\|pose&prompt=…` | Live detector switch |

All endpoints answer with `Access-Control-Allow-Origin: *`, so the portal's browser page
calls mono directly.

---

## 7. The portal

Open `http://10.42.0.128:8080/` (or `http://10.43.0.1:8080/` over the AP). It is a
single-page app served from the Pi — nothing to install on the client. Full design doc:
[`PORTAL.md`](PORTAL.md).

### Panels

| Panel | What it does |
|---|---|
| **Overview** | Live SoC temperature, throttle flags, per-service state, disk, uptime, package versions — updated over SSE |
| **Camera** | Live MJPEG from the CM3 + still capture. Lazily acquires the camera only while viewed, so it no longer steals the CM3 from mono |
| **Tracking** | The converged follow view (below) |
| **Calibration** | Guided camera-intrinsics wizard (checkerboard capture with a coverage grid, on-board OpenCV solve, reprojection-error verdict) |
| **Readiness** | Automated go / caution / no-go card: NPU identify, camera cadence, power/throttle, PTP, services, storage, thermals — downloadable as a pre-flight record |
| **Depth** | Embeds the optional `garuda-depth` stereo explorer (`:8097`, OV9281 CamArray hardware) |
| **Settings** | Bridge endpoint, PTP role, whitelisted config via configd |
| **System** | Journal excerpt viewer and reboot |

### The Tracking view (converged with mono)

mono is the perception service; the portal is a first-class client of it
(design spec: `superpowers/specs/2026-08-07-portal-tracking-integration-design.md`):

- The portal subscribes to mono's `GET /api/events` SSE feed, adapts each event's
  `tracks` into its follow-detection schema, and re-emits them on its own event bus —
  the canvas reticle, click-to-lock and drone-direction HUD all ride that feed.
- The SSE event is one JSON object per processed frame (≤10 Hz):
  `{v, age_ms, tracks: [{id, label, conf, x, y, w, h, coasting}], lock, distance:
  {raw_m, est_m, valid}, depth_engine, detector: {mode, prompt}, cal, frame, gesture}`.
- HUD additions: live distance readout + 60 s sparkline, cal add/reset buttons, a
  depth-engine toggle, a COCO class dropdown and a free-text prompt box (world mode),
  and a mono power button. Clicks and class/prompt changes POST to mono's API directly
  from the browser (CORS `*`), not through the portal.
- In pose mode the portal draws skeletons and a gesture chip, and acts server-side:
  STOP → follow paused, FOLLOW ME → re-lock to the gesturing track, POINT → HUD only.
- Failure modes: mono down → offline banner + auto-retry (portal stays up); malformed
  track events are dropped per-detection; a failed prompt/engine switch leaves the
  current state untouched and surfaces the error.

### Architecture & security posture

Two processes, separated by a privilege boundary: the unprivileged `garuda-portal`
(Python stdlib HTTP server) talks to root-run `garuda-configd` over a Unix socket; configd
executes **only whitelisted operations** (schema-validated config get/set, `garuda-*`
service start/stop) and audit-logs everything else as rejected.

> **⚠️ Bench tool, isolated LAN only.** The portal (`:8080`) and mono API (`:8098`) are
> **unauthenticated** — any host on the LAN can change config and start/stop services.
> Additionally, the bench account holds passwordless sudo (install.sh grants it), so the
> portal/configd privilege split is architectural intent here, not enforced containment.
> Two hard gates stand before exposing this anywhere beyond an isolated bench LAN:
> **(1)** authentication on both services, **(2)** a non-sudo service account. Tracked in
> `decisions.md` (2026-07-30) and the portal-tracking spec.

---

## 8. Networking

Two access paths; they can coexist.

### A. Direct Ethernet (PC ↔ Pi), NetworkManager shared profile

On the **PC**, create a shared profile on the interface cabled to the Pi (example name
`eno1` — substitute yours):

```bash
nmcli connection add type ethernet ifname eno1 con-name pi-share ipv4.method shared
nmcli connection up pi-share
```

`ipv4.method shared` puts the PC at `10.42.0.1`, runs a DHCP server and NATs the Pi to
the internet through the PC. The Pi DHCPs into `10.42.0.x` — on this bench it lands at
**`10.42.0.128`** (find yours with `nmcli` or `arp -n` if different, then pin it by MAC
in your DHCP config if you want it stable).

```bash
ssh garuda@10.42.0.128
```

Access URLs on this link: portal `http://10.42.0.128:8080` · mono `http://10.42.0.128:8098`.

### B. WiFi access point ("GarudaBench")

The Pi can serve its own hotspot so a phone/laptop reaches the GUIs with no cabling:

```bash
# on the Pi — choose your OWN passphrase (>= 8 chars); it is stored only in
# NetworkManager's config on the Pi and must NEVER be committed to git
./hosts/pi5/setup_wifi_ap.sh --psk '<your own passphrase>'
```

- SSID `GarudaBench` (override with `--ssid`), AP address **`10.43.0.1/24`**, clients
  DHCP onto `10.43.0.x`.
- Default band is 5 GHz (channel 36), which requires the regulatory country the script
  sets (`--country`, default US). **If a client cannot see the SSID, re-run with
  `--band bg`** (2.4 GHz, channel 6).
- The script is idempotent (re-running updates the existing `garuda-ap` profile) and
  supports `--dry-run`.

Access URLs over the AP: portal `http://10.43.0.1:8080` · mono `http://10.43.0.1:8098`.

Note: the Ethernet shared link and the AP are independent; the AP serves `10.43.0.x`
regardless of the state of the `10.42.0.x` cable link.

---

## 9. Benchmarks & phase gates

### The phase-gate system

Progress is structured as phases **P1–P6**, each closed by a **gate**: a measured
pass/fail number, taken by a harness on this machine, recorded in `bench/benchlog.md`
with a `bench/versions.md` snapshot, and red-teamed before ticking (was it throttled?
underpowered? the wrong clock? a cherry-picked dataset?).

| Phase | Gate | Status |
|---|---|---|
| **P1 NPU sanity** | identify OK; model-zoo YOLO ≥30 fps on sample video; an LLM answering via hailo-ollama | **✅ ticked 2026-07-29** |
| **P2 capture** | CM3 streaming; later: GS pair triggered, inter-camera jitter <100 µs, 1 h no-drop at 30 fps | open (GS cameras not yet on bench) |
| **P3 time** | ptp4l stable; offset <50 µs RMS through a switch (sub-µs point-to-point first); trigger from the disciplined clock | open |
| **P4 VIO** | ≥20 Hz front end, ≥200 Hz pose, <20 ms latency (live, GS pair) | open (dataset groundwork done) |
| **P5 bridge** | odometry → EKF2 external vision on the real FC; VIO-kill → covariance failsafe | open (SITL half complete) |
| **P6 load** | VIO + detection + VLM concurrent, no VIO frame deficit, no throttling | open |

### Measured results (every number from `bench/benchlog.md`, with its date)

| Date | Measurement | Result |
|---|---|---|
| 2026-07-29 | `hailortcli fw-control identify` | PASS — HAILO10H, FW 5.1.1 |
| 2026-07-29 | `hailortcli benchmark` yolov8m (NPU streaming) | 76.23 fps |
| 2026-07-29 | **P1 gate**: end-to-end video pipeline (decode+pre+infer+post, yolov8m) | **75.95 fps, 0 drops** (fresh boot, flags 0x0 before and after) |
| 2026-07-29 | LLM via hailo-ollama (`qwen2:1.5b`) | answered correctly; 39 tokens, 5.13 s server-side |
| 2026-07-29 | PTP point-to-point, software-timestamped grandmaster | mean −8.8 ns, RMS 3.60 µs (GM jitter dominates — not the Pi's HW-PTP capability) |
| 2026-07-30 | CM3 delivered-frame metrics (30 s/mode, no encode) | 120.12 / 56.02 / 14.00 fps, 0 drops in all three modes |
| 2026-07-30 | Basalt VIO, EuRoC MH_01 (dataset, headless) | RMS ATE 0.0783 m, 43.0 fps |
| 2026-07-30 | Basalt VIO, TUM-VI room1 (dataset, headless) | 61.0 fps, RMS ATE 0.1104 m |
| 2026-08-04 | garuda-track: CM3 capture→detect→track front end | median 29.6 fps, `get_throttled` 0x0 |
| 2026-08-07 | garuda-mono detector loops, 2 boots (camera set to 15 fps) | primary 12.89 / 12.99 fps; world (open-vocab) 14.79 / 14.96 fps — *run 1 taken under a logged under-voltage flag; the supply has since been replaced* |
| 2026-08-07 | yolo_world_v2s standalone inference (idle NPU) | ~36 ms/frame |
| 2026-08-07 | 960² yolo26m rebuild gate (≥15 fps floor) | **FAIL** — 7.35 / 7.40 fps (2 boots, throttle clean 0x0→0x0); the deployed detector stays at 640² |
| 2026-08-07 | yolo26m-depth production HEF deploy probe | PASS — logit std 0.263 vs ~0.04 collapse signature |
| 2026-08-10 | 27 W PSU verification | negotiated 5000 mA; `get_throttled` 0x0 with mono hot; EXT5V 5.168 V |
| 2026-08-10 | Shared-pipeline fps, all detector modes (pose/primary) | 9.96–9.98 fps — the ~10 fps ceiling is the shared per-frame pipeline, **not** the models (pose adds no measurable cost) |

### Known unmeasured items (TBD-bench)

- **y26d metric-depth physical validation** — move-test on unseen viewpoints +
  tape-measure accuracy run. Until then the metric scale is unvalidated.
- Post-gating 4 m gesture re-verification.
- Sustained-load thermal behaviour on the 27 W supply (power ≠ thermals; the passive
  heatsink's ~84 °C throttle ceiling is still real).
- 1 h no-drop capture soak (P2 wording).

When you re-measure anything: record it in `bench/benchlog.md` with the date and a
`versions.md` snapshot. If a doc needs a number you don't have, write `TBD-bench`.

---

## 10. Troubleshooting

### Camera not detected — full diagnostic procedure

Symptom: the mono GUI or portal camera view is blank/stuck, but the services report
healthy. Work down this ladder on the Pi:

**1. Does libcamera see a camera?**

```bash
rpicam-hello --list-cameras
```

Expected: `imx708` with its mode list. `no cameras available` → continue.

**2. Did any sensor answer the boot-time I2C probe?**

```bash
sudo vclog --msg | grep BSC
```

`BSC_A`/`BSC_B` lines reading **"no ACK"** mean **no sensor answered the boot I2C probe
on either CSI connector** — the firmware probed both and heard silence.

**3. Force a runtime probe and read the kernel's verdict:**

```bash
sudo dtoverlay imx708 cam0     # probe the CAM0 connector
# (run without the cam0 parameter to probe CAM1 instead)
dmesg | tail -30
```

- `imx708 ... error -5` (I/O error) and/or `dw9807 ... error -121` (the camera's focus
  chip) = **the chips are not answering electrically** — the sensor is unreachable on the
  wire, not a software/config problem.
- A clean probe here but no camera at step 1 points instead at config
  (`camera_auto_detect` / overlay) — remove any diagnostic overlays afterwards:
  `sudo dtoverlay -r imx708`.

**4. Conclusion matrix:**

| Pi side (`rp1-cfe` in dmesg) | I2C result | Conclusion |
|---|---|---|
| healthy | silence on **both** connectors (no ACK, −5/−121) | **Cable or camera-module fault.** Power off fully, reseat both ends checking orientation (gold contacts toward Ethernet at the Pi end, toward the PCB at the camera end). **Swap the cable first** — it is the cheaper, more common culprit — then the camera module |
| healthy | clean probe | Software/config: check `camera_auto_detect=1` in `/boot/firmware/config.txt`, stray `dtoverlay` lines, and whether another process holds the camera (below) |
| rp1-cfe errors | — | Pi-side CSI/bring-up problem — recheck `config.txt` and the OS install before blaming hardware |

This exact ladder diagnosed the bench's own first-light failure (benchlog 2026-07-29):
cable orientation/seating, shown by I2C −5 no-ACK on both ports until a correct attach.

**Also check: who owns the camera?** The CM3 is single-client. If the GUI is blank but
the camera probes fine:

```bash
ps aux | grep rpicam-vid
```

Exactly one `rpicam-vid` should be running (garuda-mono's). Two services fighting for the
camera leaves one with a `<defunct>` capture child — restart `garuda-mono` so it
re-acquires.

### Hailo device missing / NPU errors

- `lspci | grep -i hailo` empty → power off and **reseat the PCIe FFC**.
- In `lspci` but `hailortcli fw-control identify` fails → **version mismatch** between
  kernel module, pcie driver and HailoRT. On kernel ≥6.15 with the 5.3.0 driver, confirm
  the `timer_delete_sync` patch was applied and dkms built ([§4](#the-hailort-530-stack-what-this-snapshot-targets)).
- Identify reports a non-10H architecture → wrong package family; purge any plain
  `hailort`/`hailo-all` and reinstall `h10-*` only.
- `HAILO_OUT_OF_PHYSICAL_DEVICES (74)` → a second process tried to open the NPU. A plain
  VDevice seizes it exclusively; only one NPU-using process may run at a time (garuda-mono
  shares all its models inside one process for exactly this reason). Stop the other
  service.
- vDMA timeout −110 or Gen3 flakiness → the symptom-gated fixes in [§4](#known-pitfalls--apply-only-on-symptom-never-pre-emptively).
- `Cannot create log file hailort.log` in service logs is benign (read-only cwd).

### Thermal throttling / power

```bash
vcgencmd get_throttled
vcgencmd measure_temp
```

Decode: `0x0` = clean. Low bits = happening **now** (bit 0 under-voltage, bit 1 ARM
frequency capped, bit 2 currently throttled, bit 3 soft temp limit). High bits = sticky
"has occurred since boot" (bit 16 under-voltage, bit 17 freq-cap, bit 18 throttle,
bit 19 soft temp limit). Common readings on this bench: `0x50000` = an under-voltage
occurred (supply problem — fix the PSU, do not trust any perf number from that boot);
`0xe0000` = thermal throttling occurred (passive heatsink doing its best).

- Sustained CV/NPU load plateaus ~84 °C and throttles — expected with passive cooling.
  Cap frame rates, pause between runs, or add active cooling.
- Repeated brownouts / unexplained reboots under load → verify the 5 V/5 A contract
  ([§2](#2-hardware-assembly)). Do not apply the EEPROM current override.

### Service logs

```bash
journalctl -u garuda-mono -f
journalctl -u garuda-portal -u garuda-configd --since -1h
journalctl -b -1    # previous boot — only if the journal is persistent
```

The default install keeps the journal in RAM, so a crash/reboot destroys the evidence.
To make it persistent (recommended on a bench that is chasing power/stability issues):

```bash
sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald
```

### Mono GUI shows `searching...` forever

Not a fault by itself — it means no detection of the selected class is in frame. The
detector genuinely returns zero detections on a blank wall. Point the camera at a person
or object, lower `GARUDA_MONO_CONF`, or try a different target class before suspecting
the pipeline.

---

## 11. Licence

Project code is **BSD 3-Clause** ([`../LICENSE`](../LICENSE)).

### Dependency / model policy

- **Product code (`services/`): BSD / MIT / Apache / LGPL-dynamic only.** The CI licence
  scan (`ci/`) **fails hard on GPLv3 in `services/`** — a failing scan is a stop, not an
  override. Every new dependency states its licence in the commit/PR description and
  drops a skeleton bitbake recipe in `yocto/meta-garuda/`.
- **Bench/experimental/evaluation work: GPLv3/AGPL permitted**, under a deliberate,
  logged relaxation (`decisions.md` 2026-08-02; running software locally is not
  distribution). Guardrails: GPL components stay **behind the plugin interfaces**
  (`ICaptureBackend` / `IFrontEnd` / `IDepthSource`), are **tagged
  `not-product-cleared`**, and are never deep-wired into shippable `services/`.
- **`models/` is the evaluation zone** — any licence may be fetched and benchmarked
  there. `models/LICENCES.md` is the manifest: each model is marked ship-eligible or
  eval-only; only clean-licence models graduate into `services/`. HEF binaries are
  git-ignored and fetched by script.

Concretely, on this bench: the Ultralytics model family (YOLO26m detect/depth,
yolov8m_pose, yolo_world_v2s) is **AGPL-3.0/GPL-3.0 — evaluation only, not product
cleared**. DepthAnything V2 (Apache-2.0), fast_depth (MIT), ByteTrack (MIT, vendored),
OpenCV (Apache-2.0), and HailoRT (LGPL, dynamically linked) are clean. linuxptp (GPLv2)
is used strictly as an external daemon, never linked. Product-tier licence-cleanliness
remains the goal: a licence-clean detector and depth model must replace the eval models
before anything ships.
