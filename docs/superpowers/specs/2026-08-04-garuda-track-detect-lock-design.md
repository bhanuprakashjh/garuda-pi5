# S1 — garuda-track: detection + multi-object tracking + operator lock (design)

**Date:** 2026-08-04 · **Status:** design, pending review · **Machine:** Pi 5 + Hailo-10H (bench)

## Goal

A live perception service that detects all objects (COCO) on the CM3 color camera, tracks them across
frames, lets the operator **click one in the portal to lock** onto it, and publishes both the full track
list and the locked target as a stable contract for downstream services. First of four sub-projects toward
"detect + track a target, measure its distance, avoid obstacles, follow it"; the later three (distance,
obstacle sectors, follow/avoid control) consume S1's output and are **out of scope here**.

## Context & fit

- Companion computer; the FC is a separate PX4 board (not on the bench). S1 is pure perception — no control,
  no FC, no GARUDA_SIM path needed yet.
- Reuses proven pieces: Hailo YOLOv8m (75.95 fps measured, benchlog P1), the Hailo-decode + latest-wins
  mmap IPC pattern from `services/garuda-mask` (SLAM masker), and the existing `garuda-portal` for the UI.
- Cameras: CM3/IMX708 color (rolling shutter — fine for detection at low angular rate). The OV9281 mono pair
  and cross-camera calibration are **not** needed by S1 (they belong to S2/S3).

## Architecture

```
CM3 (IMX708) ──► Hailo YOLOv8m @640 ──► ByteTrack ──► Lock FSM ──► TrackedObjects IPC (latest-wins mmap)
   ICaptureBackend    (NPU decode)      (Python)      │  ▲              │
                                                       │  └── lock cmd ──┤
                                                       │     (click)     │
                                          garuda-portal live overlay ◄───┘  (MJPEG: frame + boxes, locked highlighted)
```

Single Python service (`services/garuda-track`), native on the Pi (ROS wrapper deferred — keep ROS thin per
CLAUDE.md §3). Main loop: capture → detect → track → FSM update → write IPC; a side channel receives lock
commands. The portal reads the IPC to draw the overlay and posts lock commands back.

## The contract (goes in `interfaces/`)

`TrackedObjects` (one per processed frame; the interface S2/S4 depend on):

| field | type | notes |
|---|---|---|
| `t_capture_ns` | uint64 | frame capture time (PTP domain later) |
| `frame_w`, `frame_h` | int | detection-frame resolution the bboxes are in |
| `objects[]` | list | each: `track_id:int`, `cls:int`, `cls_name:str`, `conf:float`, `bbox:[x,y,w,h]px`, `center:[cx,cy]px`, `age:int`, `state:{tentative,confirmed,coasting}` |
| `locked_id` | int | track id of the locked target, `-1` if none |
| `lock_state` | enum | `SEARCHING \| LOCKED \| COASTING \| LOST` |
| `seq` | uint64 | monotonic write sequence (seqlock for lockless reads) |

**Transport:** JSON serialized into a fixed-size mmap region (POSIX shm), single writer, seqlock (bump `seq`
odd before write / even after) so readers get a consistent snapshot without blocking — the `garuda-mask`
`MaskIPC` pattern, generalized to a variable-length JSON payload with a size header.

**Lock command channel** (portal → service): small JSON messages — `{cmd:"lock", x, y}` (frame-space click;
service locks the top-most track whose bbox contains the point), `{cmd:"lock_id", id}`, `{cmd:"unlock"}`.
Transport: a POSIX shm mailbox or a local UDS socket (decide in the plan; must be non-blocking on the
service side).

## Components & decisions

- **Detector:** Hailo YOLOv8m @640, COCO-80. Reuse the `garuda-mask` Hailo-decode path (NMS + box decode).
  Output: boxes + class + score. (YOLOv11s/m is a candidate Pareto upgrade — a benchlog re-measure item,
  not a blocker.)
- **Tracker:** **ByteTrack (MIT)** — vendored lean implementation (Kalman + two-stage IoU association, high-
  and low-score), no GPL, minimal deps. Runs on CPU alongside the NPU detector. *Alternatives:* Norfair
  (BSD, simpler) as a fallback; Hailo's in-pipeline `hailotracker` (LGPL) as a later perf swap.
- **Lock FSM:**
  - `SEARCHING` — no lock; tracking everything, waiting for a lock command.
  - `LOCKED` — following `locked_id`; each frame the target's track is present and confirmed.
  - `COASTING` — target track missing for ≤ `N_coast` frames (brief occlusion): keep `locked_id`, keep
    publishing last-known + `state:coasting`; re-lock immediately if the id reappears (ByteTrack's own
    lost-track buffer handles short IoU/Kalman re-association).
  - `LOST` — missing > `N_coast`: drop the lock → `SEARCHING`. (Longer-loss appearance re-ID via OSNet is a
    **deferred increment**, not in S1.)
- **Portal integration:** `garuda-portal` gains a live view — an MJPEG stream (server-side draws the current
  boxes from the IPC, highlights the locked one), and a click handler that maps the click to frame
  coordinates and posts `{cmd:"lock", x, y}`. Click-again / an "unlock" button clears it. (MJPEG + click
  overlay follows the `hosts/pi5/camarray_focus.py` live-server pattern.)

## Placement

- `services/garuda-track/` — `track_service.py` (main loop), vendored `bytetrack.py`, `lock_fsm.py`,
  `track_ipc.py` (writer), `tests/`.
- `interfaces/` — `tracked_objects.(py|schema)` — the `TrackedObjects` message + lock-command schema.
- `services/garuda-portal/` — live detection/track view + click-to-lock (extends the existing portal).
- Behind a detection plugin boundary consistent with `IFrontEnd`/`ICaptureBackend` (no Pi-specific calls
  outside `hosts/pi5/`).

## Bench realities (must handle)

- **NPU + camera are single-tenant.** `garuda-depth` holds the Hailo device + camera on boot; S1 needs them
  exclusively → the run script stops `garuda-depth` first (documented, idempotent). Only one of {depth, track}
  runs at a time for now.
- **Thermal / passive cooling.** Sustained YOLO throttles the passive Pi 5. Cap/measure fps; record fps, CPU,
  and `vcgencmd get_throttled` in `bench/benchlog.md` (numbers are sacred — no estimates).
- **Rolling shutter (CM3):** acceptable for detection; note box jitter under fast rotation as a known limit.

## Licensing

ByteTrack (MIT), OpenCV (Apache-2.0), Hailo tooling — all clean under CLAUDE.md §6; **no GPL**, no bench
relaxation needed for S1. State licences in the landing commit; no new Yocto recipe required unless a
pip dependency lands (then add a skeleton recipe per rule 6).

## Testing

- **Unit (CI, no hardware):** ByteTrack association on synthetic detection sequences (id continuity, id
  switches under crossing); lock-FSM transitions (SEARCHING↔LOCKED↔COASTING↔LOST incl. the coasting timeout);
  click→track selection (point-in-bbox, top-most-on-overlap); IPC seqlock round-trip. Mark camera/NPU tests
  `@requires_hw(camera, npu)` so CI skips cleanly.
- **Live harness (bench):** run on the CM3; measure end-to-end fps, CPU core-time, `get_throttled`, and lock
  stability (frames-to-reacquire through a staged brief occlusion, false-lock rate). Also run on a recorded
  clip for a deterministic regression.

## Success criteria

1. Live on the bench: detect COCO objects + stable multi-object tracks on the CM3 in real time.
2. Operator clicks a box in the portal → that target locks; the locked target is highlighted and published
   with a stable `track_id`.
3. Lock survives a brief occlusion (coasting) and re-acquires; drops cleanly to SEARCHING after a long loss.
4. `TrackedObjects` published over the IPC and consumed by a simple reader (proves the contract for S2/S4).
5. fps / CPU / thermal measured and recorded in `bench/benchlog.md`.

## Non-goals / deferred

- Distance-to-target (S2), obstacle sectors (S3), follow/avoid control (S4).
- Cross-camera (IMX708↔OV9281) calibration — an S2/S3 prerequisite (P0), not S1.
- Appearance re-ID (OSNet) for long-loss re-acquisition — a later S1 increment.
- Concurrent depth+track (single-tenant NPU for now); ROS 2 wrapper; YOLOv11 swap; hailotracker perf swap.

## Open items / TBD-bench

- Measured detect+track fps, CPU, and throttle on the CM3 (numbers → benchlog).
- Lock-command transport choice (shm mailbox vs UDS) — settle in the plan.
- Whether portal overlay draws server-side (simplest) or client-side — settle in the plan.
