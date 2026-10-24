# Stereo Target Range Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Report the metric distance of the locked target from the OV9281 stereo pair (real triangulation, not a monocular size guess), surfaced in the portal with a `stereo`/`est.` badge, at low CPU.

**Architecture:** Extend `garuda-depth` (owns the OV9281 CamArray + `StereoRig` + Hailo vdevice) with a range mode gated on a locked target. It runs yolov8m (HailoRT-python) on the stereo pair, selects the best instance of the locked class, samples disparity in the target's bbox (ROI-only, not a full dense map), and publishes `Z = f·B/disparity` over HTTP. The portal sends the locked class on lock and polls the range; it badges the value `stereo` when valid and falls back to the existing CM3 size-estimate (`est.`) otherwise.

**Tech Stack:** Python 3.13, OpenCV (Apache-2.0), HailoRT-python (LGPL-dyn), existing `StereoRig`/`CamArrayBackend`, vanilla JS portal.

## Global Constraints

- **Numbers are sacred.** Every fps/CPU/accuracy figure comes from a harness run on this Pi, recorded in `bench/benchlog.md` with date + `versions.md` snapshot. Unknown = `TBD-bench`; never invented.
- **Licence-clean / product-eligible.** Only OpenCV (Apache-2.0) + the yolov8m detection HEF already in the stack. **No GPL** — must NOT depend on the eval-only StereoNet/scdepthv3 nets. New dependency ⇒ state licence + skeleton recipe in `yocto/meta-garuda/`.
- **Single-tenant camera.** `garuda-depth` owns the OV9281 CamArray; no second owner.
- **Idempotent `hosts/pi5/` scripts.** Safe to re-run.
- **Passive cooling.** Cap compute rate; log `vcgencmd get_throttled` (0x0 = OK) in every bench run.
- **Small commits, tested.** Each module lands with its unit test/harness; hardware-only tests marked `@requires_hw(camera_gs)` and skip cleanly.
- **Sudo/service actions** (restart `garuda-depth`, deploy to Pi) are proposed to the human before running.

---

## File Structure

- `hosts/pi5/target_range.py` *(new, pure)* — association + `Z=f·B/d`. No cv2/Hailo. Fully unit-tested.
- `hosts/pi5/roi_disparity.py` *(new)* — bbox-ROI block-match → median disparity (cv2). Unit-tested with synthetic shifts.
- `hosts/pi5/hailo_detect.py` *(new)* — yolov8m HailoRT-python wrapper; testable `decode()` + live `infer()`.
- `hosts/pi5/camarray_depth_explorer.py` *(modify)* — gated range thread + `/api/range_target`, `/api/target_range`.
- `services/garuda-portal/static/{index.html, app.js, follow_viz.js}` *(modify)* — POST locked class, poll range, badged readout.
- `hosts/pi5/test_target_range.py`, `hosts/pi5/test_roi_disparity.py`, `hosts/pi5/test_hailo_detect.py` *(new)* — unit tests.
- `hosts/pi5/range_bench.py` *(new)* — live accuracy + CPU/fps/thermal harness.
- `docs/decisions.md` *(modify)* — Task 1 spike findings.
- `bench/benchlog.md` *(modify)* — Task 7 measured results.

**Dependency order:** Task 1 (spike, unblocks 3) → Tasks 2 & 4 (pure, parallel-safe) → Task 3 (needs 1) → Task 5 (needs 2,3,4) → Task 6 (needs 5) → Task 7 (needs all, live).

---

### Task 1: NPU multi-tenancy spike + yolov8m HEF resolution

**Files:**
- Modify: `docs/decisions.md` (append findings)
- (No production code; this is a discovery gate)

**Interfaces:**
- Produces: confirmed **yolov8m detection HEF path** usable from HailoRT-python; its **output format** (on-chip NMS vs raw tensors) with a minimal working infer snippet; a **go/no-go on concurrent NPU use** (garuda-inference CM3 yolov8m + a second yolov8m from garuda-depth), and the fallback if contention is bad.

- [ ] **Step 1: Locate the yolov8m detection HEF.** On the Pi, find the HEF the GStreamer inference pipeline uses:
  ```bash
  ssh garuda@10.42.0.128 'grep -rhoE "[^ ]+\.hef" /opt/garuda/garuda-inference/ ~/garuda-sdk-deploy/ 2>/dev/null | sort -u; \
    find / -iname "*yolov8m*.hef" 2>/dev/null'
  ```
  Record the path. Confirm it is licence-acceptable (same model already in the stack).

- [ ] **Step 2: Confirm the output format.** Inspect the HEF's output vstreams:
  ```bash
  ssh garuda@10.42.0.128 'hailortcli parse-hef <path-to-yolov8m.hef>'
  ```
  Note whether outputs are NMS-decoded (per-class `[ymin,xmin,ymax,xmax,score]`) or raw grid tensors. This sets the `decode()` in Task 3. Review `hosts/pi5/hailo_yolo_seg.py` for the existing decode pattern to reuse.

- [ ] **Step 3: Minimal HailoRT-python infer.** Write a throwaway script (in the scratchpad, not the repo) that loads the HEF on a vdevice and runs one inference on a saved OV9281 frame; confirm it returns detections. Capture the exact API calls used (they seed Task 3).

- [ ] **Step 4: Concurrent-NPU check.** With `garuda-inference` running (CM3 yolov8m active), run the Step 3 script concurrently. Observe: does the second net load and infer, or does HailoRT error / block? Note whether the HailoRT multi-process scheduler is required (`HAILORT_ENABLE_SERVICE` / scheduler env). Record fps of both under contention.

- [ ] **Step 5: Record decision.** Append to `docs/decisions.md`: HEF path, output format, concurrent-NPU verdict, and the chosen path — either (a) concurrent OK at `RANGE_FPS=6`, or (b) fallback: lower `RANGE_FPS` / time-slice / serialize. Commit.

  ```bash
  git add docs/decisions.md && git commit -m "spike: NPU multi-tenancy + yolov8m HEF for stereo range (Task 1)"
  ```

**Gate:** If concurrent NPU use is impossible even at reduced rate, STOP and escalate — the design's core assumption fails and needs revisiting before Tasks 3/5.

---

### Task 2: `target_range.py` — association + metric geometry (pure)

**Files:**
- Create: `hosts/pi5/target_range.py`
- Test: `hosts/pi5/test_target_range.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `Detection = namedtuple("Detection", "class_id label conf x y w h")` (bbox in pixels); `RangeResult = namedtuple("RangeResult", "z_m conf count valid reason")`; `z_from_disparity(focal_px, baseline_mm, disparity_px) -> float(m)`; `select_target(detections, target_class) -> (Detection|None, int)`; `range_target(focal_px, baseline_mm, detections, target_class, disparity_fn) -> RangeResult` where `disparity_fn(Detection) -> float|None`. (Refines the spec's signature to a fully pure form — `disparity_fn` closes over the rig/images in the live wiring, keeping this module hardware-free.)

- [ ] **Step 1: Write the failing tests.** `hosts/pi5/test_target_range.py`:

```python
import pytest
from target_range import Detection, z_from_disparity, select_target, range_target


def det(label, conf, x=0, y=0, w=10, h=10, cid=0):
    return Detection(cid, label, conf, x, y, w, h)


def test_z_from_disparity_known():
    # f=1000px, B=60mm, disp=20px -> 1000*60/20 = 3000mm = 3.0m
    assert z_from_disparity(1000.0, 60.0, 20.0) == pytest.approx(3.0)


@pytest.mark.parametrize("bad", [0.0, -5.0, None])
def test_z_from_disparity_rejects_nonpositive(bad):
    with pytest.raises(ValueError):
        z_from_disparity(1000.0, 60.0, bad)


def test_select_target_picks_max_conf_and_counts():
    dets = [det("person", 0.6), det("person", 0.9), det("car", 0.8)]
    best, count = select_target(dets, "person")
    assert best.conf == 0.9 and count == 2


def test_select_target_absent():
    best, count = select_target([det("car", 0.8)], "person")
    assert best is None and count == 0


def test_range_target_ok():
    r = range_target(1000.0, 60.0, [det("person", 0.9)], "person", lambda d: 20.0)
    assert r.valid and r.reason == "ok" and r.z_m == pytest.approx(3.0) and r.count == 1


def test_range_target_no_class():
    r = range_target(1000.0, 60.0, [det("car", 0.8)], "person", lambda d: 20.0)
    assert (not r.valid) and r.reason == "no_class"


def test_range_target_bad_disparity():
    r = range_target(1000.0, 60.0, [det("person", 0.9)], "person", lambda d: None)
    assert (not r.valid) and r.reason == "bad_disparity" and r.count == 1
```

- [ ] **Step 2: Run to verify it fails.** `cd hosts/pi5 && python3 -m pytest test_target_range.py -v` → FAIL (no module `target_range`).

- [ ] **Step 3: Implement `hosts/pi5/target_range.py`:**

```python
"""Pure association + metric geometry for stereo target ranging. No cv2/Hailo:
the disparity source is injected as `disparity_fn` so this is fully unit-tested."""
from collections import namedtuple

Detection = namedtuple("Detection", "class_id label conf x y w h")   # x,y,w,h in pixels
RangeResult = namedtuple("RangeResult", "z_m conf count valid reason")


def z_from_disparity(focal_px, baseline_mm, disparity_px):
    """Metres from a rectified-pixel disparity. Z_mm = f*B/d."""
    if disparity_px is None or disparity_px <= 0:
        raise ValueError(f"disparity must be > 0, got {disparity_px!r}")
    return (focal_px * baseline_mm / disparity_px) / 1000.0


def select_target(detections, target_class):
    """(highest-confidence detection of target_class, count of that class)."""
    matches = [d for d in detections if d.label == target_class]
    if not matches:
        return None, 0
    return max(matches, key=lambda d: d.conf), len(matches)


def range_target(focal_px, baseline_mm, detections, target_class, disparity_fn):
    """Select the target, sample its disparity, convert to metres.
    reason in {ok, no_class, bad_disparity}. count>1 does NOT invalidate."""
    best, count = select_target(detections, target_class)
    if best is None:
        return RangeResult(None, 0.0, 0, False, "no_class")
    disp = disparity_fn(best)
    if disp is None or disp <= 0:
        return RangeResult(None, best.conf, count, False, "bad_disparity")
    return RangeResult(z_from_disparity(focal_px, baseline_mm, disp),
                       best.conf, count, True, "ok")
```

- [ ] **Step 4: Run tests to verify pass.** `python3 -m pytest test_target_range.py -v` → all PASS.

- [ ] **Step 5: Commit.**
```bash
git add hosts/pi5/target_range.py hosts/pi5/test_target_range.py
git commit -m "feat: target_range pure association + metric Z geometry (Task 2)"
```

---

### Task 3: `roi_disparity.py` — bbox-ROI disparity (cv2)

**Files:**
- Create: `hosts/pi5/roi_disparity.py`
- Test: `hosts/pi5/test_roi_disparity.py`

**Interfaces:**
- Consumes: rectified left/right grayscale images + a bbox `(x,y,w,h)` in pixels.
- Produces: `roi_disparity(rect_left, rect_right, bbox, num_disp=64, block=7, min_valid=40) -> float|None` (median valid disparity in px inside the bbox; `None` if too few valid pixels). This is the `disparity_fn` builder for the live wiring in Task 5.

- [ ] **Step 1: Write the failing tests.** `hosts/pi5/test_roi_disparity.py`:

```python
import pytest
np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
from roi_disparity import roi_disparity


def test_recovers_known_shift():
    # Rectified convention: a feature at column x in LEFT appears at x-D in RIGHT
    # for disparity D. Synthesize that and check the recovered disparity ~ D.
    rng = np.random.default_rng(0)
    left = (rng.random((120, 320)) * 255).astype(np.uint8)
    D = 16
    right = np.zeros_like(left)
    right[:, : left.shape[1] - D] = left[:, D:]
    d = roi_disparity(left, right, (170, 40, 60, 40), num_disp=32, block=7)
    assert d is not None and abs(d - D) <= 2


def test_none_on_flat_region():
    flat = np.full((120, 320), 128, np.uint8)
    assert roi_disparity(flat, flat, (170, 40, 60, 40)) is None
```

- [ ] **Step 2: Run to verify it fails.** `python3 -m pytest test_roi_disparity.py -v` → FAIL (no module).

- [ ] **Step 3: Implement `hosts/pi5/roi_disparity.py`:**

```python
"""Bbox-ROI stereo disparity: block-match only a band around the target so the
cost stays tiny (no full-frame dense map). Returns median valid disparity (px)."""
import cv2
import numpy as np

_sgbm_cache = {}


def _sgbm(num_disp, block):
    key = (num_disp, block)
    if key not in _sgbm_cache:
        _sgbm_cache[key] = cv2.StereoSGBM_create(
            minDisparity=0, numDisparities=num_disp, blockSize=block,
            P1=8 * block * block, P2=32 * block * block, uniquenessRatio=10)
    return _sgbm_cache[key]


def roi_disparity(rect_left, rect_right, bbox, num_disp=64, block=7, min_valid=40):
    x, y, w, h = (int(round(v)) for v in bbox)
    H, W = rect_left.shape[:2]
    x0 = max(0, x - num_disp)          # left-pad for the search range
    x1 = min(W, x + w)
    y0, y1 = max(0, y), min(H, y + h)
    if (x1 - x0) < block or (y1 - y0) < block:
        return None
    disp = _sgbm(num_disp, block).compute(
        rect_left[y0:y1, x0:x1], rect_right[y0:y1, x0:x1]).astype(np.float32) / 16.0
    bx0 = x - x0                        # bbox region inside the crop
    roi = disp[:, bx0:bx0 + w]
    valid = roi[roi > 0]
    if valid.size < min_valid:
        return None
    return float(np.median(valid))
```

- [ ] **Step 4: Run tests to verify pass.** `python3 -m pytest test_roi_disparity.py -v` → PASS. (If cv2 absent on the dev box, run on the Pi.)

- [ ] **Step 5: Commit.**
```bash
git add hosts/pi5/roi_disparity.py hosts/pi5/test_roi_disparity.py
git commit -m "feat: roi_disparity bbox-only stereo disparity (Task 3)"
```

---

### Task 4: `hailo_detect.py` — yolov8m HailoRT-python wrapper

**Files:**
- Create: `hosts/pi5/hailo_detect.py`
- Test: `hosts/pi5/test_hailo_detect.py`

**Interfaces:**
- Consumes: the HEF path + output format from **Task 1**; a shared `vdevice`; `Detection` from `target_range`.
- Produces: `HailoDetector(hef_path, vdevice=None, conf_thresh=0.4, labels=COCO)` with `.infer(image) -> list[Detection]` (grayscale replicated to 3ch; bboxes in input-image pixels) and `.close()`; plus a pure `decode_nms(raw, labels, in_w, in_h, conf_thresh) -> list[Detection]` that is unit-tested with a mocked raw output.

> Adapt the decode to Task 1's confirmed format. The code below assumes the common Hailo **on-chip NMS** output: a per-class list of `(N,5)` arrays `[ymin,xmin,ymax,xmax,score]` in normalized coords (the format `hailo_yolo_seg.py` already handles). If Task 1 found raw tensors, port `hailo_yolo_seg.py`'s decode instead and update the test's mock to match.

- [ ] **Step 1: Write the failing test** (decode only — no hardware). `hosts/pi5/test_hailo_detect.py`:

```python
import pytest
np = pytest.importorskip("numpy")
from hailo_detect import decode_nms


def test_decode_nms_pixels_and_threshold():
    labels = ["person", "car"]
    raw = [
        np.array([[0.10, 0.20, 0.50, 0.40, 0.90],   # person, conf 0.90
                  [0.00, 0.00, 0.10, 0.10, 0.20]]),  # person, conf 0.20 -> dropped
        np.array([[0.30, 0.30, 0.60, 0.70, 0.80]]),  # car, conf 0.80
    ]
    dets = decode_nms(raw, labels, in_w=640, in_h=480, conf_thresh=0.4)
    assert len(dets) == 2
    person = [d for d in dets if d.label == "person"][0]
    assert person.conf == pytest.approx(0.90)
    # xmin*w=0.20*640=128, ymin*h=0.10*480=48, w=(0.40-0.20)*640=128, h=(0.50-0.10)*480=192
    assert (round(person.x), round(person.y), round(person.w), round(person.h)) == (128, 48, 128, 192)
```

- [ ] **Step 2: Run to verify it fails.** `python3 -m pytest test_hailo_detect.py -v` → FAIL.

- [ ] **Step 3: Implement `hosts/pi5/hailo_detect.py`** (structure below; fill `infer()` from Task 1's confirmed API, mirroring `hailo_stereo.py`):

```python
"""yolov8m object detection via HailoRT-python, for running detection on the
OV9281 stereo pair inside garuda-depth. decode_nms() is pure and unit-tested."""
import numpy as np
from target_range import Detection

COCO = [ ... ]  # 80-class COCO labels (copy from hailo_yolo_seg.py / model card)


def decode_nms(raw, labels, in_w, in_h, conf_thresh):
    """Hailo on-chip-NMS output -> Detections in input-image pixels.
    raw: per-class list of (N,5) arrays [ymin,xmin,ymax,xmax,score] normalized."""
    dets = []
    for class_id, arr in enumerate(raw):
        if arr is None or len(arr) == 0:
            continue
        for ymin, xmin, ymax, xmax, score in np.asarray(arr):
            if score < conf_thresh:
                continue
            dets.append(Detection(
                class_id, labels[class_id], float(score),
                xmin * in_w, ymin * in_h, (xmax - xmin) * in_w, (ymax - ymin) * in_h))
    return dets


class HailoDetector:
    def __init__(self, hef_path, vdevice=None, conf_thresh=0.4, labels=None):
        self.labels = labels or COCO
        self.conf_thresh = conf_thresh
        # Load HEF on vdevice, configure infer model, cache in_w/in_h.
        # (Exact HailoRT calls come from Task 1's confirmed snippet; mirror hailo_stereo.py.)
        ...

    def infer(self, image):
        """Run yolov8m on one image (grayscale -> 3ch). Returns list[Detection]."""
        if image.ndim == 2:
            image = np.repeat(image[:, :, None], 3, axis=2)
        # resize to (in_w,in_h), run, collect raw NMS output, then:
        raw = ...  # HailoRT output for this frame
        return decode_nms(raw, self.labels, self.in_w, self.in_h, self.conf_thresh)

    def close(self):
        ...
```

- [ ] **Step 4: Run the decode test to verify pass.** `python3 -m pytest test_hailo_detect.py -v` → PASS.

- [ ] **Step 5: Live smoke on the Pi** (proposed to human): run `.infer()` on one saved OV9281 frame; confirm sane detections. This is `@requires_hw` and not part of CI.

- [ ] **Step 6: Commit.**
```bash
git add hosts/pi5/hailo_detect.py hosts/pi5/test_hailo_detect.py
git commit -m "feat: yolov8m HailoRT-python detector + tested NMS decode (Task 4)"
```

---

### Task 5: Wire the range mode into `garuda-depth`

**Files:**
- Modify: `hosts/pi5/camarray_depth_explorer.py`

**Interfaces:**
- Consumes: `target_range.range_target`, `roi_disparity.roi_disparity`, `hailo_detect.HailoDetector`, existing `StereoRig` (`focal_px`, `baseline_mm`, `rectify`, `order_for_matching`) and `_state["L"/"R"]`, `_shared_vdevice()`.
- Produces: HTTP `POST /api/range_target?class=<name>` (empty/absent clears) and `GET /api/target_range` → `{class,z_m,conf,count,valid,reason,age_ms}`.

- [ ] **Step 1: Add range state + a monotonic timestamp field** near the other module globals:
```python
_range = {"class": None, "result": None, "t": 0.0}   # locked class -> latest RangeResult
_range_lock = threading.Lock()
RANGE_FPS = max(1.0, float(os.environ.get("GARUDA_RANGE_FPS", "6")))
```

- [ ] **Step 2: Add a gated `range_loop`** (new thread), mirroring `depth_loop`'s frame access + fps cap. Idle while no class set; else detect → range → store:
```python
def range_loop(rig, models_dir):
    detector = None
    min_period = 1.0 / RANGE_FPS
    last = 0.0
    while True:
        with _range_lock:
            cls = _range["class"]
        if cls is None:
            time.sleep(0.2); continue
        if detector is None:
            detector = HailoDetector(YOLOV8M_HEF, vdevice=_shared_vdevice())
        with _lock:
            L, R = _state["L"], _state["R"]
        if L is None:
            time.sleep(0.05); continue
        wait = min_period - (time.monotonic() - last)
        if wait > 0: time.sleep(wait)
        last = time.monotonic()
        rc1, rc2 = rig.rectify(L, R)
        left, right = rig.order_for_matching(rc1, rc2)
        dets = detector.infer(left)
        res = range_target(rig.focal_px, rig.baseline_mm, dets, cls,
                           lambda d: roi_disparity(left, right, (d.x, d.y, d.w, d.h)))
        with _range_lock:
            _range["result"], _range["t"] = res, time.monotonic()
```
  `YOLOV8M_HEF` = the path from Task 1. Start the thread in `main()` alongside `depth_loop`.

- [ ] **Step 3: Add the endpoints** in the handler's `do_POST`/`do_GET`:
```python
# do_POST: /api/range_target?class=person   (no class -> clear -> idle)
with _range_lock:
    _range["class"] = (parse_qs(path.query).get("class", [""])[0] or None)
    if _range["class"] is None:
        _range["result"] = None
self._send_json({"ok": True})

# do_GET: /api/target_range
with _range_lock:
    r, t, cls = _range["result"], _range["t"], _range["class"]
age = int((time.monotonic() - t) * 1000) if t else None
body = ({"class": cls, "valid": False, "reason": "no_class"} if r is None else
        {"class": cls, "z_m": r.z_m, "conf": r.conf, "count": r.count,
         "valid": r.valid, "reason": r.reason, "age_ms": age})
self._send_json(body)
```

- [ ] **Step 4: Syntax check + deploy + live verify** (proposed to human — restarts `garuda-depth`, single-tenant camera):
```bash
python3 -m py_compile hosts/pi5/camarray_depth_explorer.py
# deploy to Pi, restart garuda-depth, then:
curl -s -X POST 'http://127.0.0.1:8097/api/range_target?class=person'
curl -s 'http://127.0.0.1:8097/api/target_range'   # expect valid=true with a plausible z_m when a person is in view
curl -s -X POST 'http://127.0.0.1:8097/api/range_target?class='   # clear -> idle
```
  Confirm: valid range with a person in the stereo FoV; `valid=false, reason=no_class` when cleared or class absent; CPU stays within budget (`top`).

- [ ] **Step 5: Commit.**
```bash
git add hosts/pi5/camarray_depth_explorer.py
git commit -m "feat: gated stereo target-range mode + endpoints in garuda-depth (Task 5)"
```

---

### Task 6: Portal — send locked class, poll range, badged readout

**Files:**
- Modify: `services/garuda-portal/static/index.html` (badge element)
- Modify: `services/garuda-portal/static/app.js` (poll `/api/target_range`, POST class on lock/clear on unlock)
- Modify: `services/garuda-portal/static/follow_viz.js` (render stereo value + badge, else keep CM3 est.)

**Interfaces:**
- Consumes: `:8097/api/range_target`, `:8097/api/target_range`; the follow pipeline's locked target + class; existing `#follow-distance` and `rangeFromH`.
- Produces: badged distance UI — `stereo` (with `1/N` if `count>1`) vs `est.`

- [ ] **Step 1: Add a badge span** next to `#follow-distance` in `index.html` (e.g. `<span id="follow-dist-badge" class="dist-badge"></span>`), plus minimal CSS in `style.css` for `.dist-badge` (stereo = accent, est. = muted).

- [ ] **Step 2: On lock/unlock**, in the follow lock handler (`set_pinned` path) in `app.js`: when a target is locked, `fetch(`${depthBase}/api/range_target?class=${encodeURIComponent(cls)}`, {method:'POST'})`; on unlock, POST with empty class. Guard with try/catch (depth svc may be down).

- [ ] **Step 3: Poll the range** at ~3 Hz in `app.js` (only while inference is active): `GET :8097/api/target_range` → store `LIVE.stereoRange = {valid, z_m, count, age_ms}`.

- [ ] **Step 4: Render** in `follow_viz.js` `onLiveFollow`: if `LIVE.stereoRange?.valid && age_ms < 1000`, set `#follow-distance` to `z_m.toFixed(1)` m and badge `stereo`(+`· 1/N` if count>1); else keep the existing `rangeFromH` value and badge `est.`. Never blank the number.

- [ ] **Step 5: Verify.** `node --check app.js && node --check follow_viz.js`; run the portal's `services/garuda-portal/tests/` (routes still serve); load the portal, lock a target, confirm the badge flips `stereo`↔`est.` as the target enters/leaves the stereo view.

- [ ] **Step 6: Commit.**
```bash
git add services/garuda-portal/static/
git commit -m "feat: portal badged stereo/est. target distance readout (Task 6)"
```

---

### Task 7: Live accuracy + CPU bench, record in benchlog

**Files:**
- Create: `hosts/pi5/range_bench.py`
- Modify: `bench/benchlog.md`

**Interfaces:**
- Consumes: the running range endpoints; a target at tape-measured distances.
- Produces: a benchlog entry with measured accuracy, CPU, fps, temp, `get_throttled`.

- [ ] **Step 1: Write `range_bench.py`** — sets a class, samples `/api/target_range` N times at each of several held distances, prints median `z_m` + spread per station; separately samples garuda-depth CPU (`top`), `RANGE_FPS`, SoC temp, and `vcgencmd get_throttled`. Idempotent, `--help`.

- [ ] **Step 2: Run the accuracy protocol** (proposed to human; needs a person/object at tape-measured 1/2/3 m in the stereo FoV). Record measured `z_m` vs ground truth → error at each station.

- [ ] **Step 3: Red-team the numbers** (rule 9). If error grows with distance or is biased, the OV9281 stereo calibration is the prime suspect (not the ranging code) — state this explicitly and, if needed, flag recalibration as follow-up. Confirm `get_throttled` = 0x0 and no NPU-contention frame deficit vs Task 1's baseline.

- [ ] **Step 4: Write the benchlog entry** with date + `versions.md` snapshot: accuracy table, CPU (target < ~0.5 core), fps, temp, throttle, and the concurrent-NPU verdict. Any figure not measured = `TBD-bench`.

- [ ] **Step 5: Commit.**
```bash
git add hosts/pi5/range_bench.py bench/benchlog.md
git commit -m "bench: stereo target-range live accuracy + CPU (Task 7)"
```

---

## Self-Review (completed)

- **Spec coverage:** metric Z (T2), ROI disparity avoiding dense-map cost (T3), NPU detection (T4) with the multi-tenancy risk gated first (T1), gated endpoints (T5), badged stereo/est. UI (T6), ground-truthed accuracy + CPU gate (T7). All spec sections mapped.
- **Type consistency:** `Detection(class_id,label,conf,x,y,w,h)` and `RangeResult(z_m,conf,count,valid,reason)` are defined in T2 and used unchanged in T3/T4/T5; `roi_disparity(...)->float|None` matches the injected `disparity_fn(Detection)->float|None` via the T5 closure; endpoint JSON keys match between T5 (produce) and T6 (consume).
- **Placeholders:** the only `...`/`TBD` are (a) Task 4's `infer()` body and `COCO`, explicitly deferred to Task 1's confirmed HailoRT API/labels, and (b) `TBD-bench` per the numbers rule — both intentional, not gaps.

---

## AMENDMENT 2026-08-04 — Option B (single-process stereo) after the Task 1 gate

Task 1 proved cross-process NPU sharing is unavailable on this stack (no HailoRT multi-process service
daemon). Owner chose **Option B**: do detection + single-target lock + ranging **entirely in
garuda-depth on the OV9281 stereo pair** (one process, one yolov8m — no NPU conflict, no system change).
See `docs/decisions.md` 2026-08-04.

**Unchanged:** Tasks 2 (`target_range.py`), 3 (`roi_disparity.py`), 4 (`hailo_detect.py`) — these are the
B foundation exactly as written. Task 7 (bench) unchanged in intent.

**Task 5 (revised for B):** garuda-depth range mode runs yolov8m on the rectified stereo-LEFT, selects the
single best instance of the active class (set via `/api/range_target?class=`, or by a click→nearest-
detection endpoint), ranges it via `roi_disparity` on L/R → `target_range`, and **overlays boxes + the
metric Z onto its existing :8097 MJPEG**. `garuda-inference` (CM3) is NOT run concurrently in this mode.

**Task 6 (revised for B):** the portal's stereo-range view shows the :8097 stream (boxes + Z overlay) and
lets the user pick the class / click to lock; it shows the metric Z. **Drop the CM3 mono "est." fallback**
(no concurrent CM3) — when the target isn't detected or disparity is bad, show "searching / no range".

**Deferred to P6:** concurrent CM3 colour tracking + stereo ranging (needs the HailoRT multi-process
service, i.e. Option A/A″).

**Licence:** yolov8m (Ultralytics) is AGPL-3.0 — same detector already on the bench (P1), tag
`not-product-cleared`. The ranging code is Apache/clean. (Corrects the spec's "product-eligible" phrasing.)
