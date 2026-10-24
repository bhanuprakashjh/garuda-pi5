# Baseline: Basalt VIO on TUM-VI room1 — Pi 5, headless

**Date:** 2026-07-30 · **Committed provenance:** every number from a run on this bench.

## Setup

- **Basalt:** mateosss/basalt fork @ `df6e970c8da7636eb401a09e3317fbeaaf829b9a` (BSD-3; see
  docs/decisions.md 2026-07-30), built native on the Pi: GCC 14.2.0 (Trixie),
  `--preset=release` + `CXX_MCPU=cortex-a76`, `CXX_MARCH=armv8.2-a+crc+dotprod+fp16+rcpc`, `-j2`
- **Dataset:** TUM-VI `dataset-room1_512_16` (euroc-format export, 2821 stereo frames,
  162 s), from cvg.cit.tum.de. (EuRoC MH_01 pending — robotics.ethz.ch outage 2026-07-30;
  retry unit armed on the Pi.)
- **Invocation** (fork's own CI methodology): `basalt_vio --dataset-path … --cam-calib
  tumvi_512_eucm_calib.json --dataset-type euroc --show-gui 0 --config-path
  tumvi_512_config.json --result-path … --save-trajectory tum`
- **Power/thermals:** `get_throttled` `0x0` before AND after the measured run (reboot-cleaned;
  a prior validation run on the same boot as the -j2 build carried sticky `0x50000` from the
  build and is not counted). Supply: PD charger — still not the verified 5 V/5 A unit.

## Numbers (measured run)

| Metric | Value |
|---|---|
| RMS ATE (SE3-aligned, 2772 associations) | **0.1104 m** |
| Frames | 2821 |
| Exec time (tool-reported) | 46.265 s |
| **Front-end throughput** | **60.97 fps** |
| Real-time factor (162 s sequence / 47.6 s wall) | **3.4×** |
| CPU utilisation (user 149.9 s / wall 47.6 s) | ~3.1 cores |

Validation run (same boot as build, sticky UV flags — recorded for repeatability only):
56.2 s wall / ATE 0.1088 m — consistent with the measured run.

## EuRoC MH_01_easy (added same day, after ETH outage workaround)

Dataset: ASL-format zip from the ETH Research Collection `machine_hall.zip` bundle
(12.68 GB, DSpace bitstream API — direct server was down all day). Same binary, invocation
per fork CI with `euroc_config.json` + `euroc_ds_calib.json`. Flags `0x0` before AND after.

| Metric | Value |
|---|---|
| RMS ATE (SE3-aligned, 3638 associations) | **0.0783 m** |
| Published Basalt desktop VIO on MH_01 | 0.07 m (DM-VIO paper comparison table / community docs) |
| **Ratio vs published — plan criterion "within 2×"** | **1.12× — PASS** |
| Frames | 3682 |
| Exec time | 85.66 s → **43.0 fps** |
| Real-time factor (~184 s sequence) | **2.1×** |
| CPU | ~3.4 cores |

Archival re-run (same day, artifacts in `mh01_artifacts/`): **ATE 0.0711 m = 1.02× of
published**, peak RSS **206 MB**. Wall time 183.5 s on this run — the cam-portal
(rpicam + NPU postprocess) was streaming concurrently; CPU user-time matched the idle run,
wall doubled. First accidental datapoint for the P6 contention question. Run-to-run ATE
spread (0.0783 vs 0.0711) = threading nondeterminism; both at the published figure.
Stats-file gotcha for the future runner: basalt writes trajectory/stats to the **cwd** —
run from a writable per-run directory or they vanish silently.

## Reading against the Phase 4 gate

- Gate asks ≥20 Hz live stereo front end: dataset throughput is **3× that** with NPU idle.
  Live camera adds capture/sync overhead; margin is large but the live number is TBD-bench.
- ATE 2×-of-desktop criterion is defined against EuRoC in the plan — formal comparison
  deferred until MH_01 lands. room1 at 0.110 m is in the expected range for this pipeline.
- Per-stage ms breakdown: not emitted by this binary's default output — needs the fork's
  stats instrumentation or a profiler pass; TBD-bench, tracked for the P4 proper work.
