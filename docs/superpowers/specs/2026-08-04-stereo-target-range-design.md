# Stereo Target Range — Design Spec

**Date:** 2026-08-04
**Status:** Approved for planning
**Owner workstream:** Perception follow/avoid — sub-project S2 (distance), v1 slice

## 1. Goal

Report the **metric distance of the currently-locked target** using the OV9281
global-shutter stereo pair (a *real* triangulated measurement, not a monocular
size guess), surfaced live in the Garuda portal, at low CPU, with the existing
CM3 size-estimate as a clearly-labeled fallback.

One sentence: *turn the locked CM3 target into a real `Z` in metres via the
stereo pair's baseline, on the NPU, without pegging the passively-cooled Pi.*

## 2. Why (context)

- A single camera has no scale: monocular distance can only be *estimated* from
  an assumed object size (what the portal's current `range ≈ 0.6 / bbox_height`
  readout does) or a ground-plane assumption. It is not a measurement.
- Metric distance requires a baseline: `Z = f·B / disparity`. The OV9281 pair is
  rigid on the CamArray board with a known baseline and an existing calibration
  (`bench/camarray_stereo.yaml`, consumed by `StereoRig`, already prints real cm
  via SGBM).
- The earlier CPU spike came from rendering + colorizing + JPEG-encoding a
  **full dense depth map** every frame. For one target we only need disparity at
  the object → a bbox-ROI match, which structurally avoids that cost.

## 3. Global Constraints (bind every task in the plan)

- **Numbers are sacred.** Every fps/CPU/accuracy figure comes from a harness run
  on this Pi and is recorded in `bench/benchlog.md` with date + `versions.md`
  snapshot. Unknown numbers are written `TBD-bench`, never invented.
- **Licence-clean / product-eligible.** This feature uses only OpenCV
  (Apache-2.0) + the yolov8m detection HEF already in the stack. **No GPL** — it
  must NOT depend on the eval-only StereoNet / scdepthv3 depth nets. If any new
  dependency lands, state its licence and drop a skeleton bitbake recipe in
  `yocto/meta-garuda/`.
- **Single-tenant camera.** `garuda-depth` owns the OV9281 CamArray; do not spawn
  a second owner of that device.
- **Idempotent `hosts/pi5/` scripts.** Any run/deploy script is safe to re-run.
- **Passive cooling.** Cap compute rate; log `vcgencmd get_throttled` (0x0 = OK)
  in any bench run.
- **Small commits, tested.** Each module lands with its unit test or harness;
  hardware-only tests are marked `@requires_hw(camera_gs)` and skip cleanly.

## 4. Scope

**In scope (v1):**
- Single dominant target of the locked *class* in the stereo view.
- Real stereo `Z` when the target is detectable in the pair; labeled CM3
  size-estimate fallback otherwise.
- Portal readout badged so the user always knows *stereo* vs *est.*

**Out of scope (deferred):**
- **Multi-object disambiguation** of two instances of the same class — needs the
  CM3↔OV9281 extrinsic calibration (P0, rigid mount). v1 picks the
  highest-confidence instance and reports the instance `count` so the UI can flag
  ambiguity.
- **CM3↔stereo geometric association** (projecting the exact CM3 box into the
  pair) — same P0 dependency.
- Obstacle sectors (S3) and follow/avoid control (S4).

## 5. Architecture

Extend `garuda-depth` (`hosts/pi5/camarray_depth_explorer.py`) — it already owns
the OV9281 CamArray, holds a `StereoRig`, and manages the shared Hailo vdevice.
Add a **range mode**, gated on a locked target (no class set → no compute → idle,
same discipline as the opt-in depth panel).

```
CM3 track/lock  (existing: garuda-inference + portal follow)
      │ locked target's class
      ▼
Portal ──POST :8097/api/range_target?class=<name>──► garuda-depth
                                                       range thread (≤ RANGE_FPS, gated on class set):
                                                         1. read latest stereo pair (already captured)
                                                         2. rectify (StereoRig.rectify — already done for display)
                                                         3. yolov8m on LEFT rectified image (HailoRT-python)
                                                         4. filter to locked class → select best single instance
                                                         5. bbox-ROI block match → median disparity
                                                         6. Z = focal_px·baseline_mm / disparity
                                                         7. publish {z_m, conf, count, valid, reason, t}
Portal ◄──GET :8097/api/target_range (poll ~3 Hz)─────┘
   render: valid → "4.2 m  ⬤ stereo"
           else  → "~3.8 m  ⬤ est."   (existing CM3 rangeFromH)
```

## 6. Components & interfaces

### 6.1 `hosts/pi5/hailo_detect.py` (new)
HailoRT-python yolov8m object-detection wrapper. Mirror the existing
`hailo_yolo_seg.py` / `hailo_stereo.py` load+infer+decode pattern (reuse its yolo
decode where possible).

```python
Detection = namedtuple("Detection", "class_id label conf x y w h")  # x,y,w,h in PIXELS of the input image

class HailoDetector:
    def __init__(self, hef_path, vdevice=None, conf_thresh=0.4): ...
    def infer(self, image_gray_or_bgr) -> list[Detection]:
        """Run yolov8m on one image (grayscale is replicated to 3ch). Returns
        detections in input-image pixel coords, filtered by conf_thresh."""
    def close(self): ...
```
- Consumes: a yolov8m detection HEF compatible with HailoRT-python decode.
- Produces: `Detection` list used by `target_range`.

### 6.2 `hosts/pi5/target_range.py` (new, pure/testable)
Association + geometry. No camera or Hailo dependency in the pure functions so
they unit-test without hardware.

```python
def select_target(detections, target_class) -> tuple[Detection | None, int]:
    """Return (best-confidence detection of target_class, count of that class)."""

def z_from_disparity(focal_px, baseline_mm, disparity_px) -> float:
    """Metres. Z_mm = focal_px*baseline_mm/disparity; /1000. disparity<=0 -> raise."""

RangeResult = namedtuple("RangeResult", "z_m conf count valid reason")

def range_target(rig, rect_left, rect_right, detections, target_class,
                 disparity_fn) -> RangeResult:
    """Glue: select_target -> disparity_fn(bbox) -> z_from_disparity.
    disparity_fn(rect_left, rect_right, bbox) -> median valid disparity (px) or None.
    reason ∈ {ok, no_class, bad_disparity}. Ambiguity (count>1) does NOT
    invalidate — it is surfaced via `count` for the UI to badge."""
```
- `disparity_fn` is injected so tests feed a synthetic disparity; the live wiring
  passes a bbox-ROI block-matcher built on `StereoRig`.

### 6.3 `hosts/pi5/camarray_depth_explorer.py` (modify)
- New gated **range thread**: idles while `_range["class"] is None`; otherwise
  runs the loop above at `RANGE_FPS` (env `GARUDA_RANGE_FPS`, default 6).
- yolov8m net loaded once (lazily, on first class set) on the shared vdevice;
  kept resident.
- New HTTP endpoints:
  - `POST /api/range_target?class=<name>` — set active class; empty/absent class
    clears it (→ idle). Returns `{"ok": true}`.
  - `GET  /api/target_range` — `{"class", "z_m", "conf", "count", "valid",
    "reason", "age_ms"}`. `valid=false` when no class set, no detection, or bad
    disparity.

### 6.4 `services/garuda-portal/static/{app.js, follow_viz.js, index.html}` (modify)
- On lock (target pinned) → `POST :8097/api/range_target?class=<locked class>`;
  on unlock → clear.
- Poll `GET :8097/api/target_range` at ~3 Hz.
- Render in the follow readout (`#follow-distance`): if `valid`, show `z_m` with a
  **stereo** badge; else keep the existing `rangeFromH` mono value with an **est.**
  badge. If `count > 1`, mark the stereo value ambiguous (e.g. "stereo · 1/N").
- Never let the number vanish — fallback is always available.

## 7. Algorithms

- **Association (v1):** among detections whose label == locked class, pick max
  confidence; `count` = number of that class. (No geometric match — deferred P0.)
- **Disparity (ROI, not full-frame):** crop a horizontal band around the target
  bbox in both rectified images, block-match within it, take the **median of
  valid disparities** inside the bbox. ROI-limited → cheap. Invalid if too few
  valid pixels.
- **Metric:** `Z_m = (focal_px · baseline_mm / disparity_px) / 1000`.

## 8. Edge cases

| Situation | Behaviour |
|---|---|
| No class set (no lock) | range thread idle; `valid=false` |
| Locked class not detected in stereo | `valid=false, reason=no_class` → portal shows est. |
| >1 instance of the class | pick best conf; report `count` → portal badges ambiguity |
| Low-texture / occluded in one view (bad disparity) | `valid=false, reason=bad_disparity` → est. |
| Stale (age > ~1 s) | portal treats as invalid → est. |
| Target outside stereo FoV | same as "not detected" → est. |

## 9. Budgets

- yolov8m on the pair capped at `RANGE_FPS` (default 6). Target garuda-depth
  range-mode CPU **< ~0.5 core** (reference: fast_depth measured 0.4 core @ 6 fps,
  2026-08-04 benchlog). Actual = `TBD-bench`.
- Portal poll ~3 Hz (negligible).

## 10. Testing

- **Unit (`target_range.py`, no hardware):** `select_target` picks max-conf and
  counts; `z_from_disparity` matches `f·B/d` for known inputs and rejects
  `disparity<=0`; `range_target` returns the right `reason`/`valid` for
  no-class / ambiguous / bad-disparity / ok via an injected `disparity_fn`.
- **Unit (`hailo_detect.py`):** decode of a mocked HailoRT output → expected
  `Detection`s; live inference `@requires_hw(camera_gs)`.
- **Live accuracy (Pi, recorded in `benchlog.md`):** place a target at
  tape-measured distances (e.g. 1 / 2 / 3 m); compare stereo `Z` to ground truth;
  record error, CPU, fps, temp, `get_throttled`. *"Real distance" must be
  ground-truthed, not asserted.*

## 11. Risks

- **NPU multi-tenancy (primary).** Runs a *second* yolov8m on the Hailo (stereo)
  while garuda-inference runs the first (CM3) — concurrent cross-process NPU use,
  unproven on this bench (effectively the P6 concurrent-load question, early).
  **Plan step 1 is a spike** to verify HailoRT multi-process sharing; fallback =
  time-slice / lower `RANGE_FPS` if contention is bad.
- **yolov8m detection HEF for HailoRT-python.** The GStreamer pipeline's HEF may
  need a python-decodable variant. Resolve/locate it in plan step 1 alongside the
  spike.
- **Stereo calibration accuracy bounds range accuracy.** OV9281 stereo
  calibration was previously marginal (recalibrated). If the live accuracy check
  is off, the calibration — not the ranging code — is the suspect; the accuracy
  task must red-team this.

## 12. Success criteria

1. Locking a single target shows a **stereo**-badged metric distance that tracks
   reality (accuracy figure recorded, error stated honestly).
2. Losing the target in the stereo view falls back to the **est.** badge without
   the number disappearing.
3. garuda-depth range-mode CPU stays within budget; `get_throttled` = 0x0 in the
   bench run.
4. No GPL dependency; licence scan clean.
