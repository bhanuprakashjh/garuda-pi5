# Hybrid ORB-SLAM3 (stereo) + Hailo-YOLO Dynamic Masking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a bench-only stereo VIO front end on the Pi 5 + Hailo-10H that runs ORB-SLAM3 (stereo, no IMU) with dynamic-object keypoint rejection driven by a Hailo-resident YOLOv5-seg detector, wrapped behind `IFrontEnd` so it benches head-to-head against Basalt.

**Architecture:** A **native** Hailo masking service (HailoRT + PCIe, `yolov5n_seg_h10.hef`) runs the segmenter in its own thread and publishes the latest dilated dynamic-class mask over a local IPC (latest-wins, never blocks). ORB-SLAM3's heavy CPU compute runs in an **aarch64 Ubuntu-22.04 / GCC-11 Docker container on the Pi** (documented-good toolchain — sidesteps bare-metal GCC 14), consuming the mask across the boundary and applying it clean-room inside `ORBextractor::ComputeKeyPointsOctTree`. The result implements `IFrontEnd` and is benched against Basalt on our OV9281 stereo pair.

**Tech Stack:** Python 3.13 (native masker, HailoRT InferModel API), C++14/17 (ORB-SLAM3, clean-room reimplemented from the *mechanism*, never forked), Docker (aarch64, GCC 11), OpenCV, Pangolin (headless), our existing `stereo_rig.py` / `camarray_stereo.yaml` calibration.

## Global Constraints

- **Numbers are sacred (rule 1):** every fps/ms/ATE figure comes from a harness run on THIS Pi, recorded in `bench/benchlog.md` with date + `versions.md` snapshot. Unmeasured = `TBD-bench`. Never carry a fabricated number (the source research fabricated Basalt's 61/43 fps + `yolov5n_seg` bench-path — both corrected by Phase 0 recon).
- **Licence (rule 6, under the 2026-08-02 relaxation):** ORB-SLAM3 is GPLv3; YOLOv5 (the seg model) is GPLv3 (Ultralytics). Permitted for BENCH/EXPERIMENTAL/EVALUATION only. All GPL code stays **behind `IFrontEnd`**, tagged `GPL-3.0 — not product-cleared`, with a skeleton `yocto/meta-garuda/` recipe stub. Never deep-wire into shippable `services/` beyond the plugin boundary. ORB-SLAM3 is **clean-room reimplemented from the mechanism** (verified at file/line in the research), never copied from any GPLv3 repo.
- **Clean-room rule:** the mask-injection seam is reimplemented from the described mechanism, not pasted from YOLO_ORB_SLAM3/DynaSLAM/DS-SLAM. Upstream ORB-SLAM3 itself (UZ-SLAMLab) may be built as an external GPLv3 dependency behind the interface, but our added code is original.
- **Idempotent hosts/pi5 scripts (rule 3):** any install/build script is safe to re-run; the Dockerfile is committed and built from source, never pulled-and-modified interactively (same bar as `install.sh`).
- **Hardware config (forced by what's on the bench):** stereo-only (no synced IMU), OV9281 CamArray tiles 0+1, per-pixel `yolov5n_seg` masks (not boxes).
- **FC is sacred (rule 12):** this front end publishes odometry only; nothing here reaches PX4/MAVLink. No arming, no setpoints.
- **Bench = passive heatsink:** long container compiles will run hot; cap parallelism at `-j2`, watch `vcgencmd get_throttled` during the build, log throttle events.

## Verified bench reality (Phase 0, 2026-08-02 — already done)

| Fact | Value |
|---|---|
| OS / kernel | Trixie, 6.18.34, aarch64 |
| Bare-metal toolchain | GCC/g++ **14.2.0**, CMake 3.31.6 (→ build ORB-SLAM3 in a GCC-11 container, NOT bare-metal) |
| Eigen / OpenCV (bare-metal) | 3.4.0 / 4.10.0 |
| SLAM deps bare-metal | none (Pangolin/g2o/DBoW2/Sophus/libtorch absent) |
| Seg model | `/usr/share/hailo-models/yolov5n_seg_h10.hef` (+ `yolov5seg.json`) — present |
| Detector (boxes) | `/usr/share/hailo-models/yolov8m_h10.hef`, `yolov11m_h10.hef` — present |
| Stereo calib | `bench/camarray_stereo.yaml` present on bench (baseline 47.52 mm, RMS 1.26 px) |
| Hailo | HAILO10H, FW 5.1.1, 49 °C, `throttled=0x0` |
| RAM | 7.9 Gi total, 6.8 Gi free (fine for `-j2`) |

## File Structure

```
services/garuda-mask/
├── mask_service.py          # async Hailo-seg masker; publishes latest dilated stereo mask over IPC (latest-wins)
├── mask_ipc.py              # tiny shared-memory ring: writer (masker) + reader (consumer), one latest frame
└── test_mask.py             # unit tests: mask builder + dilation + IPC latest-wins semantics
hosts/pi5/
├── hailo_yolo_seg.py        # HailoYoloSeg wrapper for yolov5n_seg_h10.hef (InferModel pattern, parses yolov5seg.json)
└── orbslam3/
    ├── Dockerfile           # aarch64 ubuntu:22.04, GCC 11, builds Pangolin(headless)+ORB-SLAM3(stereo, bUseViewer=false)
    ├── build.sh             # idempotent: build image, run EuRoC-stereo headless, tee fps/per-stage → benchlog
    ├── patches/             # clean-room mask-seam patch applied to upstream ORB-SLAM3 at image-build time
    │   └── mask-seam.md     # documents the reimplemented mechanism (provenance: research, not copied code)
    └── garuda_stereo.yaml   # ORB-SLAM3 stereo config generated from camarray_stereo.yaml (LEFT.R/P, RIGHT.R/P, Camera.bf)
services/garuda-vio/
└── orbslam3_frontend.py     # IFrontEnd impl wrapping the containerized ORB-SLAM3 via IPC (feed_stereo/feed_imu-noop/latest_pose)
calib/
└── orbslam3_config.py       # camarray_stereo.yaml -> ORB-SLAM3 garuda_stereo.yaml converter
yocto/meta-garuda/recipes-slam/
├── orb-slam3_git.bb         # skeleton stub: NAME/VERSION/LICENSE=GPL-3.0/SRC_URI (unbuilt)
└── pangolin_git.bb          # skeleton stub
docs/decisions.md            # + Phase 0 recon, build-toolchain (Docker/GCC-11) decision, GPL tagging
bench/benchlog.md            # + mask latency, ORB-SLAM3 EuRoC fps, masked-vs-unmasked, live-stereo numbers
```

---

### Task 1: Native Hailo dynamic-masking service

The licence-clean-ish, reusable, measurable first artifact. Produces a real latency number and a mask any downstream SLAM (ORB-SLAM3 OR Basalt) can consume. No GPL C++ yet; the only GPL element is the YOLOv5 model weights (tagged, behind the eval boundary).

**Files:**
- Create: `hosts/pi5/hailo_yolo_seg.py`
- Create: `services/garuda-mask/mask_ipc.py`
- Create: `services/garuda-mask/mask_service.py`
- Test: `services/garuda-mask/test_mask.py`

**Interfaces:**
- Consumes: `yolov5n_seg_h10.hef` + `yolov5seg.json` from `/usr/share/hailo-models/`; live stereo from `CamArrayBackend`; `HailoStereoNet`'s InferModel pattern (`hosts/pi5/hailo_stereo.py`) as the reference for VDevice/configure/bindings/run.
- Produces: `MaskIPC.read_latest() -> (seq:int, t_ns:int, mask:np.ndarray[H,W] uint8)` — a dilated binary mask (1 = dynamic-class, reject) for the reference (left) rectified frame, latest-wins.

- [ ] **Step 1: Write the failing test for the mask builder**

```python
# services/garuda-mask/test_mask.py
import numpy as np
from mask_service import build_dynamic_mask

def test_dynamic_class_pixels_are_masked_and_dilated():
    # fake seg output: a 4x4 person blob (class 0) in a 20x20 frame
    seg = np.zeros((20, 20), dtype=np.int32)
    seg[8:12, 8:12] = 1               # class-id 1 present
    labels = {1: "person"}            # person is a dynamic class
    mask = build_dynamic_mask(seg, labels, dynamic={"person"}, dilate_px=2)
    assert mask.dtype == np.uint8
    assert mask[10, 10] == 1                      # blob center masked
    assert mask[6, 10] == 1                       # dilated 2px above the blob (was 8 -> reaches 6)
    assert mask[0, 0] == 0                        # far corner untouched
    assert mask.sum() > 16                        # dilation enlarged the raw 16-px blob
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/garuda-mask && python3 -m pytest test_mask.py::test_dynamic_class_pixels_are_masked_and_dilated -v`
Expected: FAIL (`build_dynamic_mask` not defined / import error).

- [ ] **Step 3: Implement `build_dynamic_mask` (+ minimal `mask_service.py` scaffold)**

```python
# services/garuda-mask/mask_service.py  (builder portion)
import cv2
import numpy as np

def build_dynamic_mask(seg_labels: np.ndarray, id_to_name: dict,
                       dynamic: set, dilate_px: int) -> np.ndarray:
    """seg_labels: HxW int class-id map. Returns HxW uint8, 1 where a dynamic-class
    object is (dilated by dilate_px to absorb mask lag), else 0."""
    m = np.zeros(seg_labels.shape[:2], dtype=np.uint8)
    for cid, name in id_to_name.items():
        if name in dynamic:
            m[seg_labels == cid] = 1
    if dilate_px > 0 and m.any():
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                      (2 * dilate_px + 1, 2 * dilate_px + 1))
        m = cv2.dilate(m, k)
    return m
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/garuda-mask && python3 -m pytest test_mask.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing test for IPC latest-wins**

```python
# append to test_mask.py
from mask_ipc import MaskIPC

def test_ipc_latest_wins(tmp_path):
    w = MaskIPC(str(tmp_path / "m"), shape=(8, 8), create=True)
    r = MaskIPC(str(tmp_path / "m"), shape=(8, 8), create=False)
    a = np.ones((8, 8), np.uint8); b = np.zeros((8, 8), np.uint8)
    w.write(seq=1, t_ns=100, mask=a)
    w.write(seq=2, t_ns=200, mask=b)      # overwrite before any read
    seq, t_ns, mask = r.read_latest()
    assert seq == 2 and t_ns == 200        # reader sees ONLY the latest, never blocks/queues
    assert (mask == b).all()
```

- [ ] **Step 6: Run it, verify it fails**

Run: `python3 -m pytest test_mask.py::test_ipc_latest_wins -v` → FAIL (`mask_ipc` missing).

- [ ] **Step 7: Implement `mask_ipc.py`**

Shared-memory single-slot ring (`multiprocessing.shared_memory` or `mmap`): a small header (`seq:uint64`, `t_ns:uint64`, `h:uint32`, `w:uint32`) + the mask bytes. Writer stamps `seq` last (release semantics); reader reads `seq`, copies, re-reads `seq`, retries on tear. One latest frame only — no queue.

- [ ] **Step 8: Run all Task-1 tests → PASS**

Run: `python3 -m pytest test_mask.py -v` → all PASS.

- [ ] **Step 9: Implement the async service loop + `hailo_yolo_seg.py`**

`hailo_yolo_seg.py`: `HailoYoloSeg(hef, json)` mirroring `hailo_stereo.py` (VDevice→create_infer_model→configure→create_bindings→run), returns per-pixel class-id map (parse `yolov5seg.json` for the mask head + class list). `mask_service.py`: thread pulls latest CamArray left tile → `HailoYoloSeg.infer` → `build_dynamic_mask` → `MaskIPC.write`. Runs at the NPU's rate independent of any consumer.

- [ ] **Step 10: Harness run — record mask latency/fps to benchlog (rule 1)**

Run the service against live CamArray (or captured `calib/captures/pair*`), time N inferences. Record to `bench/benchlog.md` with date + versions snapshot: seg infer ms, end-to-end mask ms, fps, `get_throttled`, temp. Tag the YOLOv5 model licence line.

- [ ] **Step 11: Commit**

```bash
git add hosts/pi5/hailo_yolo_seg.py services/garuda-mask/ bench/benchlog.md
git commit -m "feat(mask): native Hailo yolov5n-seg dynamic-masking service with latest-wins IPC"
```

---

### Task 2: ORB-SLAM3 stereo build in aarch64 GCC-11 container (go/no-go spike)

Runbook-style (build archaeology, not TDD). Deliverable = a **measured EuRoC-stereo fps/per-stage number on this Pi** — the go/no-go for the whole idea. If ORB-SLAM3 stereo is single-digit fps here, we learned it cheaply and Basalt wins on merit.

**Files:**
- Create: `hosts/pi5/orbslam3/Dockerfile`
- Create: `hosts/pi5/orbslam3/build.sh` (idempotent)
- Create: `yocto/meta-garuda/recipes-slam/orb-slam3_git.bb`, `pangolin_git.bb` (skeleton stubs)
- Modify: `docs/decisions.md` (build-toolchain decision), `bench/benchlog.md`

**Interfaces:**
- Produces: a runnable `garuda/orbslam3:bench` image containing `stereo_euroc` (or equivalent) built with `bUseViewer=false`; a recorded EuRoC-stereo fps baseline.

- [ ] **Step 1: Write the committed Dockerfile**

`FROM ubuntu:22.04` (aarch64, GCC 11, Eigen 3.4). Install OpenCV 4.x, Eigen3, build **Pangolin headless** (`-DBUILD_PANGOLIN_GUI=OFF` where possible; runtime `PANGOLIN_WINDOW_URI=headless://`), clone upstream UZ-SLAMLab ORB-SLAM3 at a pinned tag, apply the C++14/17 bump (`s/++11/++14/g`) + `-Wno-error=deprecated-declarations` for the Eigen-3.4/g2o issue, drop the realsense2 phantom dep, build with `bUseViewer=false`, `-j2`. Pin every version. Document the GPLv3 provenance in a header comment.

- [ ] **Step 2: Build the image on the Pi (needs go-ahead — installs a large toolchain)**

`bash hosts/pi5/orbslam3/build.sh build` → `docker build`. Watch `vcgencmd get_throttled` / temp during the compile (passive heatsink). Iterate on the Eigen/g2o/GCC errors here — this is the archaeology budget (expect real hours). Record each fix + symptom in `docs/decisions.md` (rule 4: log symptom + fix).

- [ ] **Step 3: Fetch a EuRoC stereo sequence (MH_01) into `datasets/`**

Reuse `datasets/` runner conventions. Rectified stereo pair sequence for a headless run.

- [ ] **Step 4: Run ORB-SLAM3 stereo headless on EuRoC, capture timing**

Run `stereo_euroc` inside the container against MH_01. Capture wall fps + per-stage ms (tracking, stereo-match, local-BA) and ATE vs groundtruth.

- [ ] **Step 5: Record the go/no-go number to benchlog (rule 1 + rule 9 red-team)**

Write EuRoC-stereo fps/ATE + per-stage ms to `bench/benchlog.md`. Add the rule-9 red-team paragraph: thermally throttled during run? feature-count default vs tuned? cherry-picked sequence? State whether this clears/relates to the P4 gate's spirit.

- [ ] **Step 6: Drop skeleton yocto recipes + commit**

```bash
git add hosts/pi5/orbslam3/Dockerfile hosts/pi5/orbslam3/build.sh \
        yocto/meta-garuda/recipes-slam/ docs/decisions.md bench/benchlog.md
git commit -m "feat(slam): ORB-SLAM3 stereo aarch64/GCC-11 container + EuRoC baseline [GPL-3.0 not-product-cleared]"
```

**GATE:** If EuRoC-stereo fps here is far below usable (single digits, no tuning recovery), STOP and report to the human before Task 3 — the masking work is moot if the base tracker can't run.

---

### Task 3: Clean-room mask seam + stereo config from our calibration

**Files:**
- Create: `calib/orbslam3_config.py` (+ generated `hosts/pi5/orbslam3/garuda_stereo.yaml`)
- Create: `hosts/pi5/orbslam3/patches/mask-seam.md` (mechanism provenance) + the seam patch applied at image build
- Test: `calib/test_orbslam3_config.py`
- Modify: `hosts/pi5/orbslam3/Dockerfile` (apply seam patch + mount IPC), `bench/benchlog.md`

**Interfaces:**
- Consumes: `bench/camarray_stereo.yaml`; Task 1's `MaskIPC`.
- Produces: a masked ORB-SLAM3 that reads the latest dynamic mask each frame and erases masked keypoints inside `ComputeKeyPointsOctTree` (after FAST, before `DistributeOctTree`), with a per-pyramid-level dilation already applied upstream in Task 1.

- [ ] **Step 1: Write the failing test for the config converter**

```python
# calib/test_orbslam3_config.py
import numpy as np, yaml
from orbslam3_config import to_orbslam3_stereo

def test_bf_and_projection_from_calibration(tmp_path):
    calib = {"fx": 400.0, "baseline_mm": 47.52,
             "K1": [[400,0,320],[0,400,240],[0,0,1]],
             "R1": np.eye(3).tolist(), "P1": [[400,0,320,0],[0,400,240,0],[0,0,1,0]]}
    out = to_orbslam3_stereo(calib)
    assert abs(out["Camera.bf"] - 0.04752 * 400.0) < 1e-6   # baseline(m) * fx
    assert out["Camera.width"] and out["LEFT.P"]             # rectification block present
```

- [ ] **Step 2: Run it → FAIL**

Run: `cd calib && python3 -m pytest test_orbslam3_config.py -v` → FAIL.

- [ ] **Step 3: Implement `to_orbslam3_stereo` + CLI to emit `garuda_stereo.yaml`**

Read `camarray_stereo.yaml`, emit ORB-SLAM3 PinHole stereo yaml: `Camera.fx/fy/cx/cy`, distortion, `Camera.width/height`, `Camera.bf = baseline_m * fx`, `Camera.fps`, `ThDepth`, and `LEFT.K/D/R/P` + `RIGHT.K/D/R/P` for online rectification. Respect our L/R ordering (`order_for_matching` — tile0 is physical right).

- [ ] **Step 4: Run test → PASS**

- [ ] **Step 5: Author the clean-room seam patch (from mechanism, not copied)**

`patches/mask-seam.md` documents provenance (reimplemented from the described `mvDynamicArea`/`ComputeKeyPointsOctTree` mechanism — NOT copied from YOLO_ORB_SLAM3). The patch adds a `const cv::Mat* mpDynMask` member to `ORBextractor`, and one loop after per-cell FAST collection / before `DistributeOctTree`: erase any candidate keypoint whose `(x<<level)` position samples `1` in the mask. `Frame::ExtractORB` sets `mpDynMask` from the latest `MaskIPC` read. Belt-and-braces: also honor the (currently dead) `_mask` param path.

- [ ] **Step 6: Rebuild image with the patch; run masked vs unmasked on a walking-person stereo clip**

Capture a stereo clip with a person walking through a static scene (record via CamArray). Run ORB-SLAM3 stereo twice — mask IPC connected vs disconnected. Measure trajectory stability / drift delta while the person crosses frame.

- [ ] **Step 7: Record masked-vs-unmasked delta to benchlog (rule 1)**

- [ ] **Step 8: Commit**

```bash
git add calib/orbslam3_config.py calib/test_orbslam3_config.py \
        hosts/pi5/orbslam3/patches/ hosts/pi5/orbslam3/garuda_stereo.yaml \
        hosts/pi5/orbslam3/Dockerfile bench/benchlog.md
git commit -m "feat(slam): clean-room dynamic-mask seam + stereo config from our calibration"
```

---

### Task 4: `IFrontEnd` wrapper + live OV9281 + bench vs Basalt

**Files:**
- Create: `services/garuda-vio/orbslam3_frontend.py`
- Test: `services/garuda-vio/test_orbslam3_frontend.py`
- Modify: `bench/benchlog.md`, `docs/decisions.md`

**Interfaces:**
- Consumes: `interfaces/garuda_interfaces/frontend.py::IFrontEnd`, `messages.FrameMeta`/`OdometrySample`; the containerized ORB-SLAM3 over IPC; live `CamArrayBackend` stereo; Task 1's masker running alongside.
- Produces: an `IFrontEnd` implementation (`feed_stereo`, `feed_imu` no-op for stereo-only, `latest_pose` with covariance/validity per the failsafe contract) selectable against Basalt.

- [ ] **Step 1: Write the failing contract test**

```python
# services/garuda-vio/test_orbslam3_frontend.py
from orbslam3_frontend import OrbSlam3FrontEnd
from garuda_interfaces.frontend import IFrontEnd

def test_implements_ifrontend_and_pose_has_covariance():
    fe = OrbSlam3FrontEnd(config="garuda_stereo.yaml", connect=False)  # no live backend
    assert isinstance(fe, IFrontEnd)
    assert fe.latest_pose() is None                 # nothing fed yet -> None per contract
    # feed_imu is a no-op in stereo-only mode, must not raise
    fe.feed_imu(0, [0, 0, 0], [0, 0, 9.81])
```

- [ ] **Step 2: Run it → FAIL**

- [ ] **Step 3: Implement `OrbSlam3FrontEnd`**

Implements all three abstract methods. `feed_stereo` forwards the pair (+ current mask seq) to the container; `latest_pose` returns the newest `OdometrySample` with covariance/validity (invalid until tracking initializes — honoring the failsafe-gates-on-covariance rule). `feed_imu` no-ops (stereo-only). `connect=False` for headless unit testing.

- [ ] **Step 4: Run contract test → PASS**

- [ ] **Step 5: Live run — OV9281 stereo + masker + ORB-SLAM3, record fps/latency**

Full pipeline on the bench: CamArray → (masker ‖ ORB-SLAM3 container) → `latest_pose`. Record live front-end fps, pose rate, latency, `get_throttled`, temp → `bench/benchlog.md`.

- [ ] **Step 6: Decision-log entry + red-team**

`docs/decisions.md`: the hybrid result vs Basalt (note Basalt's own P4 baseline is separate, still `TBD-bench` — do not compare against fabricated numbers). Rule-9 red-team: is the pose actually better with masking, or just slower? thermally fair? Record honestly.

- [ ] **Step 7: Commit**

```bash
git add services/garuda-vio/orbslam3_frontend.py services/garuda-vio/test_orbslam3_frontend.py \
        bench/benchlog.md docs/decisions.md
git commit -m "feat(vio): ORB-SLAM3 IFrontEnd wrapper + live OV9281 stereo bench [GPL-3.0 not-product-cleared]"
```

---

## Self-Review

- **Spec coverage:** Task 1 = NPU masker (the "YOLO on NPU" half); Task 2 = ORB-SLAM3 buildable-and-measured (the base tracker + go/no-go gate); Task 3 = the actual mask injection (the "hybrid" join) + our calibration; Task 4 = the `IFrontEnd` boundary + live bench (licence guardrail + comparability). Every research finding maps to a task.
- **Numbers:** every measured value routes to `benchlog.md` as `TBD-bench` until run. No fabricated Basalt/HEF numbers carried.
- **Licence:** GPL confined behind `IFrontEnd`, tagged, yocto stubs, clean-room seam — matches the 2026-08-02 relaxation exactly.
- **Type consistency:** `MaskIPC.read_latest()`/`write()`, `build_dynamic_mask(seg, id_to_name, dynamic, dilate_px)`, `to_orbslam3_stereo(calib)`, `IFrontEnd.{feed_stereo,feed_imu,latest_pose}` — names consistent across tasks.
- **Open risk (honest):** Task 2's GCC-14→container pivot is the load-bearing bet; if the container build itself fails on aarch64/22.04, fall back to Debian-bookworm-slim (GCC 12) before bare-metal GCC 14. Task 2 has an explicit STOP gate so we don't build Tasks 3–4 on a non-viable base.
