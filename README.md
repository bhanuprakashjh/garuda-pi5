# garuda-pi5 — Garuda SDK bench tier (Raspberry Pi 5 + Hailo-10H)

Development bench for **Garuda**, a companion-computer autonomy stack for drones and
robotics. This repo is the **bench/dev tier**: a Raspberry Pi 5 with a Hailo-10H AI
accelerator (AI HAT+ 2, 40 TOPS) running live perception services — on-NPU object
detection, click-to-lock tracking, monocular metric depth, pose/gesture recognition — plus
the harnesses, calibration tooling and datasets used to measure all of it. Services are
written host-agnostic so they later port to a Hailo-15 SoM (product tier).

The flight controller (a Microchip PIC32CZ CA90 running PX4) is a **separate device**
reached over Ethernet. Hard real-time control never runs on this machine, and **nothing in
this repo arms motors** — any code that could reach PX4 defaults to a simulated endpoint.

> **Provenance:** Private snapshot of the garuda-sdk-bench monorepo, branch
> `feat/portal-tracking`, taken 2026-10-24. Large bench recordings excluded.

> **Numbers are sacred:** every performance figure in this repository was measured on the
> actual hardware and recorded in [`bench/benchlog.md`](bench/benchlog.md) with a matching
> environment snapshot in [`bench/versions.md`](bench/versions.md). Nothing is estimated;
> unmeasured values are written `TBD-bench`.

---

## What runs on the Pi

| Service | Port | What it does |
|---|---|---|
| **garuda-mono** | `:8098` | The perception service. Owns the Camera Module 3; YOLO26m detection on every frame, ByteTrack click-to-lock tracking (coast + re-acquire), monocular depth → metric distance with persistent calibration, open-vocabulary and pose detector modes, MJPEG stream + JSON/SSE API, self-served debug GUI. |
| **garuda-portal** | `:8080` | The browser configurator: system overview, camera, converged Tracking view (fed by mono's SSE feed), calibration wizard, readiness go/no-go card, settings, logs. |
| **garuda-configd** | Unix socket | Root-side whitelist-only executor behind the portal (config writes, service start/stop). |
| **garuda-depth** | `:8097` | Stereo/mono depth explorer for the optional OV9281 CamArray HAT (embedded in the portal's Depth tab). |

## Hardware at a glance

| Item | Notes |
|---|---|
| Raspberry Pi 5, 8 GB | **Passive heatsink only** — plateaus ~84 °C and throttles under sustained load; check `vcgencmd get_throttled` (0x0 = OK) |
| AI HAT+ 2 (Hailo-10H, 40 TOPS, 8 GB on-module) | Mounted via 30 mm PCIe FFC + 16 mm stacking header |
| Camera Module 3 (IMX708, rolling shutter) | CSI CAM0. **CSI is NOT hot-pluggable** — attach only with the Pi powered off |
| Power | Official Raspberry Pi 27 W (5 V ⎓ 5 A) USB-C PD supply — **verified on-bench 2026-08-10**; weaker supplies brown out under NPU+camera load |
| 256 GB microSD | Raspberry Pi OS **Trixie** 64-bit (Debian 13) |
| Optional | 2× IMX296 global-shutter cameras (VIO, sourcing), Arducam OV9281 CamArray HAT (stereo depth experiments), Arducam ToF (planned) |

Software stack: Raspberry Pi OS Trixie, HailoRT **5.3.0** (firmware + PCIe driver matched),
Python 3.13, libcamera/rpicam, linuxptp. The Hailo-10H uses the **`h10-*` package family —
never plain `hailort`/`hailo-all`** (that is the Hailo-8 line and will not drive this device).

## Quick start

Full detail with cautions in **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** — read its
hardware-assembly chapter before touching the CSI connector.

1. **Assemble powered off:** Pi 5 + AI HAT+ 2 (FFC + stacking header), Camera Module 3 on
   CAM0 (gold contacts toward the Ethernet jack at the Pi end), 27 W 5 V/5 A USB-C supply.
2. **Flash** Raspberry Pi OS **Trixie 64-bit Lite** fresh to the microSD (seed `ssh` +
   `userconf.txt` on the boot partition; bench user is `garuda`).
3. **Connect** the Pi's Ethernet directly to your PC and create a NetworkManager *shared*
   profile — the Pi lands on `10.42.0.x` (bench convention: `10.42.0.128`).
4. **Copy the repo to the Pi:**
   `rsync -a --exclude .git ./ garuda@10.42.0.128:~/garuda-sdk-deploy/`
5. **Provision:** on the Pi, run `~/garuda-sdk-deploy/hosts/pi5/install.sh` (idempotent —
   installs `hailo-h10-all`, deploys the portal/configd/depth services).
6. **Sanity check:** `hailortcli fw-control identify` must report **HAILO10H**, and
   `vcgencmd get_throttled` must read `0x0`.
7. **Fetch models** into `~/garuda-sdk-deploy/models/` (`models/fetch.sh`; the metric depth
   HEF is built on an x86 PC with `models/build_yolo26_depth_hef.sh` — never on the Pi).
8. **Enable garuda-mono** (see USER_GUIDE §5 for the unit + the `GARUDA_MONO_DEPTH`
   drop-in), then open `http://10.42.0.128:8098/` (mono GUI) and
   `http://10.42.0.128:8080/` (portal).
9. Optional: stand up the **GarudaBench WiFi AP** with
   `hosts/pi5/setup_wifi_ap.sh --psk <your own passphrase>` — clients reach the portal at
   `http://10.43.0.1:8080`.

## Documentation

- **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** — the full guide: assembly, OS + Hailo
  bring-up, deploying and using the services, networking, benchmarks, troubleshooting.
- [docs/PORTAL.md](docs/PORTAL.md) — portal design and API reference.
- [docs/decisions.md](docs/decisions.md) — dated decision log (why Trixie, why h10-only,
  known symptoms and their fixes).
- [bench/benchlog.md](bench/benchlog.md) — every measured number, with red-team notes.
- [bench/versions.md](bench/versions.md) — environment snapshots per change.

## Repository layout

| Path | Contents |
|---|---|
| `services/` | The deployable services: garuda-portal, -configd, -inference, -track, -follow, -mask, -camera. **Licence-clean only** — CI licence-scan fails hard on GPLv3 here. |
| `interfaces/` | `ICaptureBackend`, `IFrontEnd`, `IDepthSource` + message schemas — THE plugin contract. No Pi-specific calls outside `hosts/pi5/`. |
| `hosts/pi5/` | Pi-specific code: `garuda_mono.py`, Hailo model wrappers, `install.sh`, `setup_wifi_ap.sh`, systemd units, camera tooling. Idempotent scripts only. |
| `models/` | HEF fetch/build scripts + model cards + `LICENCES.md` (the evaluation zone — any licence may be *evaluated* here, only clean licences graduate to `services/`). |
| `calib/` | Stereo/intrinsics calibration tooling and targets. |
| `bench/` | Measurement harnesses, `benchlog.md`, `versions.md`. |
| `datasets/` | EuRoC/TUM-VI runners and committed baseline numbers. |
| `docs/` | Plan, portal doc, decisions log, bring-up guides, design specs, **USER_GUIDE.md**. |
| `yocto/meta-garuda/` | Skeleton bitbake recipes accreted per dependency, for the Hailo-15 product-tier port. |
| `ci/` | x86 build, unit tests, licence scan. |

## Tests

The service test suites run on any Linux box without Pi hardware (probes and cameras are
dependency-injected; hardware-only tests are marked and skipped cleanly):

```bash
python3 -m pytest services/ hosts/pi5/ -v
```

## Licence

Project code is **BSD 3-Clause** (see [LICENSE](LICENSE)).

Dependency policy: `services/` accepts **BSD / MIT / Apache / LGPL-dynamic only**; CI's
licence scan fails hard on GPLv3 there. GPLv3/AGPL components are permitted **only** for
bench/experimental/evaluation work, kept behind the plugin interfaces and tagged
`not-product-cleared` (see `models/LICENCES.md` and USER_GUIDE §11). In particular, the
Ultralytics YOLO26/YOLOv8 model family used on this bench is AGPL-3.0 — evaluation only,
never shipped.

## Security posture

The portal and mono APIs are **unauthenticated bench tools for an isolated lab LAN**. Do
not expose them beyond the bench — authentication and a non-sudo service account are hard
gates before any wider deployment (USER_GUIDE §7 and `docs/decisions.md` 2026-07-30).
