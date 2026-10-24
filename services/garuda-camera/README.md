# garuda-camera

Multi-camera capture for the Arducam **CamArray HAT (OV9281 ×4)** — global-shutter,
hardware-synchronized. The HAT presents one combined MIPI frame; this service splits
it into per-sensor **synchronized views** (shared timestamp) and derives **passive-stereo
depth**. Feeds the VIO front end (Basalt, + IMU from the FC) and `IDepthSource`
(obstacle / landing / **metric follow range**).

Bring-up (hardware not on the bench until 2026-08-01): **`docs/camarray-bringup.md`**.

## Layout
| File | Role | Deps |
|---|---|---|
| `stereo.py` | **pure** split geometry + crop + per-view metadata + `disparity_to_depth_mm` | stdlib only — dev-PC testable |
| `camarray_source.py` | `CamArraySource`: consumes an `ICaptureBackend`, emits N synced `(FrameMeta, memoryview)` views | interfaces |
| `stereo_depth.py` | `StereoDepthSource(IDepthSource)`: rectified pair → depth (uint16 mm) | numpy + cv2 (live path only) |
| `../../hosts/pi5/camarray_backend.py` | Pi libcamera `ICaptureBackend` (the only Pi-specific code) | picamera2 — **@requires_hw**, stubbed |

## Status
- ✅ Pure split/crop/metadata + stereo-depth math implemented and unit-tested (`tests/`, 8 tests, no hardware).
- ✅ `CamArraySource` glue tested with a fake backend.
- ⏳ `CamArrayBackend` (real libcamera capture) is a stub — fill in during bring-up.
- ⏳ `focal_px`/`baseline_mm` come from stereo calibration (`calib/`); no numbers invented (rule 1).

## Why this board matters
Global shutter = VIO-grade (the CM3 is rolling-shutter, dev-only). Hardware sync
largely satisfies the **P2 <100 µs jitter gate** as a hardware property (still
measure it). Two of the four views = a stereo pair for VIO; the same pair =
passive depth → the **metric range** upgrade for the mono follow controller.
