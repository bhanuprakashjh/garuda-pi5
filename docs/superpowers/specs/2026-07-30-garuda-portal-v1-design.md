# garuda-portal V1 — GUI configurator design

**Date:** 2026-07-30 · **Status:** approved in session · **Persona for V1:** integrator
bringing up a drone on the Garuda SDK board. Architecture must stage cleanly to bench-dev
and field-operator personas later.

## Goal

One browser-based portal, served from the board, where an integrator can: see the board's
health, watch the camera with live NPU detections, change the settings that matter for
integration, run a guided camera calibration, and get an honest automated go/no-go
readiness report. No installs on the client; works from a laptop or phone on the LAN.

## Architecture

Two processes, one privilege boundary:

```
browser ──HTTP/SSE/MJPEG── garuda-portal (unprivileged, python stdlib)
                                │ UNIX socket /run/garuda/configd.sock (root:garuda 0660)
                           garuda-configd (root, whitelist-only)
```

- **garuda-portal**: serves static single-page UI (plain HTML/JS, no build toolchain),
  REST API, SSE event stream, MJPEG camera stream (absorbs bench `cam_portal.py` incl. its
  frame watchdog + `Restart=always` lesson). Gathers data from existing probes:
  `vcgencmd` (temps/throttled), `hailortcli fw-control identify`, `systemctl is-active`,
  PTP state, `rpicam-hello --list-cameras`, the garuda-inference JSONL topic.
- **garuda-configd**: JSON-over-UNIX-socket; operations whitelist ONLY:
  - `config.get / config.set(key, value)` — keys validated against the config schema
  - `unit.action(unit ∈ garuda-*, action ∈ {start,stop,restart})`
  - `system.action ∈ {reboot, poweroff}`
  - anything else → rejected + audit-logged. Every accepted op audit-logged too.
- **Config store**: `/etc/garuda/config.json`, versioned schema; services read at start.
  This file is the single source of truth; portal edits go through configd only.
- The REST/stream **API is the product contract**; the plain-JS skin is replaceable by a
  fancier frontend later without backend changes.

## V1 panels

| Panel | Contents |
|---|---|
| Overview | model/serial, versions, temps, live `get_throttled` (+ sticky-flag history), service states, disk, uptime |
| Camera | live MJPEG, sensor mode picker (from list-cameras), detection-overlay toggle, capture still |
| Inference | HEF picker (h10 models on disk), garuda-inference start/stop, live detection list (SSE), NPU temp |
| Calibration | guided intrinsics wizard (below); cam-IMU + stereo cards greyed "requires hardware" |
| Readiness | go/no-go card (below), re-run button, downloadable report |
| Settings | bridge endpoint (agent IP/port), PTP role, GARUDA_SIM gate **display-only** |
| System | journal excerpt download, reboot/poweroff (via configd) |

## API surface

- `GET /api/health` — snapshot JSON
- `GET /api/events` — SSE: health ticks, detections, calib progress, readiness updates
- `GET /api/stream` — MJPEG
- `GET /api/config` / `PUT /api/config` — whitelisted keys via configd
- `POST /api/actions/{restart-service|capture-still|calib-capture|calib-solve|readiness-run|reboot}`
- `GET /api/readiness/report`, `GET /api/calibration/status`

## Calibration (V1 = guided mono intrinsics)

Flow: target setup (checkerboard dims + square size; printable PDF) → guided capture
(live corner detection overlay; 3×3 coverage grid fills as zones captured; auto-capture on
stable corners in an unfilled zone) → solve on-board (OpenCV `calibrateCamera`; new dep:
`python3-opencv`, BSD) → verdict (RMS reprojection error: <0.5 px pass, <1.0 px marginal)
→ store `/etc/garuda/calib/<sensor>.json` + history. Staged cards: cam-IMU (unlocks with
FC IMU stream), stereo extrinsics (unlocks with GS pair). No simulated calibrations.

## Readiness engine

Declarative registry; each check `{id, description, probe, severity ∈ {blocking, warning},
requires_hardware}`. V1 checks: `npu.identify`, `camera.cadence`, `power.flags`,
`power.supply5a` (permanent warning on current charger), `clock.ptp` (grey when no peer),
`services.core`, `bridge.session` (when endpoint configured), `storage.space`,
`thermals`, `calib.present_fresh`, `simgate.status`. Report: per-check
pass/warn/fail/not-applicable + one-line evidence + timestamp; rendered as the go/no-go
card; downloadable JSON as a pre-flight record. This card becomes the field-operator
`/preflight` route later, unchanged. **The portal never sends arm/offboard commands.**

## Testing

- pytest: configd whitelist rejection (the security surface), readiness registry with
  mocked probes, config schema round-trip.
- On-bench: one end-to-end readiness run on the real Pi, recorded in bench/benchlog.md.
- UI: manual checklist on bench browser + phone.

## Non-goals (V1)

Authentication (LAN-trusted bench; **hard requirement before Phase 8 ships**), OTA
(configd is its future seam), fleet/multi-board, arming control of any kind, fancy
frontend framework.

## Dependencies added

`python3-opencv` (BSD) on the Pi. Nothing on the client. No GPLv3 anywhere.
