# interfaces/ — THE contract

Every sensor and processing stage in Garuda sits behind one of these interfaces
(CLAUDE.md §1). No Pi-specific calls outside `hosts/pi5/`; no service reaches around the
contract. Pre-Phase-4 the transport is the stubbed topic layer (`topics.py`, JSONL);
at Phase 4 the same message shapes ride DDS/ROS 2 — schemas here are transport-neutral.

## Interfaces (Python ABCs, C++ headers to follow with garuda-camera)

| Interface | Implemented by | Consumed by |
|---|---|---|
| `ICaptureBackend` | hosts/pi5 libcamera backend; H15 Media Library backend later | garuda-camera |
| `IFrontEnd` | Basalt wrapper (garuda-vio) | garuda-vio pose pipeline |
| `IDepthSource` | ToF plugin (Arducam T2); stereo depth later | mapper/landing workstream |

## Message schemas (`messages.py`)

- **FrameMeta** — per-frame provenance: monotonic capture timestamp (ns) + clock domain
  tag (`ptp`/`mono`), frame id, exposure, sensor name, geometry. Every downstream message
  carries or references one. Timestamps are sacred (P3): no wall-clock, ever.
- **Detection / DetectionSet** — structured NPU output: class id + label, confidence,
  normalized xywh bbox. A DetectionSet binds detections to the FrameMeta of the frame they
  came from plus inference timing (`t_infer_done_ns`, model id). Consumers must treat an
  empty DetectionSet on a healthy stream as a *valid observation* ("nothing seen"), distinct
  from *no message* (pipeline dead) — the cam-portal wedge and VIO-freeze lessons.
- **OdometrySample** — pose + twist **+ covariance + validity flags + staleness fields**.
  The covariance/validity fields are load-bearing: the 2026-07-30 failsafe demo proved the
  FC cannot detect a frozen feed; the producer MUST degrade covariance / drop validity when
  its own health degrades (benchlog: "P5 failsafe scenarios"). A producer that cannot assess
  its own health has no business publishing odometry.

## Rules

1. Schema changes land here first, with a version bump, before any service adopts them.
2. Adding a field is minor; changing meaning/units is major — grep all services.
3. Every timestamp field name ends `_ns` and is monotonic unless suffixed `_ptp_ns`.
