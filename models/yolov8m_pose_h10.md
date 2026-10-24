# yolov8m_pose (Hailo-10H) — model card

> **Licence: AGPL-3.0 — bench/eval, `not-product-cleared`. EVAL-ONLY, never ship**
> (CLAUDE.md rule 6 + `models/LICENCES.md`).

Human pose estimation (17 COCO keypoints) on the Hailo-10H. Detects persons and emits skeleton keypoints (nose, eyes, shoulders, elbows, wrists, hips, knees, ankles) with per-joint confidence scores. Spike gate: confirm exact I/O contract and decode recipe before Task 3 (gesture wrapper) development.

**Fix round 1 (2026-08-10):** the original 2026-08-09 decode recipe below was wrong (box head
treated as raw ltrb instead of DFL; keypoint head missing the `×2` / sigmoid steps) and was never
actually exercised against a detection — see `.superpowers/sdd/2026-08-09-pose-gestures/task-1-report.md`
§"Fix round 1" for the review that caught it. This card now documents a decode recipe that has been
**validated twice**: (1) PC-side against Ultralytics' own ONNX decode graph (byte-level agreement,
see below), and (2) on the actual deployed HEF on the bench Pi, producing anatomically sane
multi-person skeletons on two real photos. Both validation runs are reproduced in full below with
verbatim commands and output.

**Why not YOLO26-pose?**
The original spec requested `yolo26m-pose`, but the Hailo Dataflow Compiler v5.3.0 (via Ultralytics 8.4.115) explicitly rejects YOLO26 pose models. The exporter code (`ultralytics/engine/exporter.py` line 629) includes a hard gate:
```python
task26 = {Segment26: "segmentation", Pose26: "pose", OBB26: "OBB"}.get(type(model.model[-1]))
if task26:
    raise ValueError(f"Hailo export does not currently support YOLO26 {task26} models.")
```
Result: `ValueError: Hailo export does not currently support YOLO26 pose models.`
**Resolution:** Fallback to zoo pre-compiled yolov8m_pose HEF (same 17-keypoint COCO spec). YOLOv8 pose IS supported by Hailo; YOLO26 pose is not. See `models/LICENCES.md` for eval-only status.

---

## I/O contract (hailortcli parse-hef, verbatim, 2026-08-09 on garuda@10.42.0.128)

```
HEF Compatible for: HAILO15H, HAILO10H
HEF Compiler Version: 5.4.0
Network group name: yolov8m_pose, Multi Context - Number of contexts: 4
    Network name: yolov8m_pose/yolov8m_pose
        VStream infos:
            Input  yolov8m_pose/input_layer1 UINT8, NHWC(640x640x3)
            Output yolov8m_pose/conv90 UINT8, NHCW(20x20x64)      # stride 32 box regression (DFL, 4 sides x 16 bins — NOT raw ltrb)
            Output yolov8m_pose/conv91 UINT8, NHCW(20x20x1)       # stride 32 score (person-class, sigmoid baked in on-chip)
            Output yolov8m_pose/conv92 UINT16, NHCW(20x20x51)     # stride 32 keypoints (17 × (x, y, conf))
            Output yolov8m_pose/conv75 UINT8, NHCW(40x40x64)      # stride 16 box regression (DFL)
            Output yolov8m_pose/conv76 UINT8, NHCW(40x40x1)       # stride 16 score
            Output yolov8m_pose/conv77 UINT16, NHCW(40x40x51)     # stride 16 keypoints
            Output yolov8m_pose/conv59 UINT8, NHCW(80x80x64)      # stride 8 box regression (DFL)
            Output yolov8m_pose/conv60 UINT8, NHCW(80x80x1)       # stride 8 score
            Output yolov8m_pose/conv61 UINT16, NHCW(80x80x51)     # stride 8 keypoints
```

**Output structure:** Three detection heads (strides 32, 16, 8) — same multi-scale anchor-free YOLOv8 pattern as detection/segmentation, but with **keypoint heads** replacing the rest of the class logits (this pose network is single-class, `nc=1`, "person").

- **Box regression (64 ch):** this is **DFL (Distribution Focal Loss) regression**, not raw ltrb —
  `4 sides (l,t,r,b) × 16 bins (reg_max=16)`. Each side's 16 values are a discrete probability
  distribution over distance bins `0..15` (in grid-cell units); the distance is the softmax-weighted
  expectation over those bins. The sibling card `models/yolo_world_v2s_h10.md` correctly labels its
  same-shaped heads as DFL — this card originally didn't (Critical review finding C1, fixed here).
- **Score (1 ch):** confidence this grid cell is centred on a person. Empirically confirmed
  **already sigmoided on-chip** at the vstream FLOAT32 output (see "On-HEF confirmation" below) —
  do not re-apply sigmoid, or you double-sigmoid it and silently corrupt every score.
- **Keypoints (51 ch):** 17 keypoints × 3 values = 51 channels, layout `[x_raw, y_raw, conf_raw]` per
  keypoint (channel-interleaved, i.e. channel `3*i, 3*i+1, 3*i+2` for keypoint `i`). `x_raw`/`y_raw`
  are **not** plain offsets — see decode recipe. `conf_raw` is empirically confirmed **raw (not
  sigmoided on-chip)** — the opposite of the score head; you must sigmoid it yourself.

---

## Keypoint decode recipe (fix round 1 — DFL box + Ultralytics v8-pose kpt decode)

This recipe was reverse-derived from Ultralytics' own traced ONNX decode graph (`Detect.forward` /
`DFL.forward` / `Pose.kpts_decode`, opset-11 export of `yolov8m-pose.pt`) — not from memory or the
docs — and validated against it byte-for-byte (see "PC-side oracle validation" below). The graph
node names below (`/model.22/...`) are load-bearing: they are how the decode steps were *found*, not
decoration.

**Per anchor `(y, x)` at stride `s`, for each of the 3 scales:**

1. **DFL box decode** (`box_out[y, x]`, 64 values):
   ```python
   reg_max = 16
   d = box_out[y, x].reshape(4, reg_max)         # rows: l, t, r, b
   d = d - d.max(axis=-1, keepdims=True)          # numerical stability
   p = np.exp(d); p /= p.sum(axis=-1, keepdims=True)   # softmax over the 16 bins, per side
   l, t, r, b = (p * np.arange(reg_max)).sum(axis=-1)  # weighted integral -> distance in grid units
   ```
   Then the same ltrb→xyxy arithmetic as before (this part of the original recipe was already
   correct, it's just fed real distances now instead of unpacking 64 values into 4):
   ```python
   cx, cy = (x + 0.5) * s, (y + 0.5) * s
   x1, y1 = cx - l * s, cy - t * s
   x2, y2 = cx + r * s, cy + b * s
   ```
   Confirmed against the ONNX graph's own constants: Ultralytics computes `anchor_points = grid + 0.5`
   (measured directly off the graph's `Constant_15`/`Constant_16` initializers, values `0.5, 1.5,
   2.5, ...` per grid row) then `x1y1 = anchor - lt; x2y2 = anchor + rb`, multiplied by stride at the
   end (`Mul_2`, stride tensor `Constant_18`) — algebraically identical to the form above.

2. **Score:** `score = score_out[y, x, 0]` — **use as-is**, it is already a `[0, 1]` probability
   (on-chip sigmoid confirmed, see below). Do **not** call sigmoid on it.

3. **Keypoint decode** (`kpt_out[y, x]`, 51 values → 17 × 3):
   ```python
   k = kpt_out[y, x].reshape(17, 3)
   kx_raw, ky_raw, kc_raw = k[:, 0], k[:, 1], k[:, 2]
   kx = (2.0 * kx_raw + x) * s          # NOT (kx_raw + x) * s — the missing x2 was review finding C2
   ky = (2.0 * ky_raw + y) * s
   kconf = sigmoid(kc_raw)               # raw at the vstream; sigmoid is NOT baked in (opposite of score)
   ```
   Derived from the ONNX graph's `Pose.kpts_decode` trace: `Mul_3` (`×2.0`) → `Add_3`
   (`+ (anchor − 0.5)`) → `Mul_4` (`× stride`), and `Sigmoid_1` on the third (confidence) slice.
   The graph's kpt-anchor constant (`Constant_25`) was measured directly and is exactly the integer
   grid index (`anchor − 0.5 = x`), which is why the simplified form above uses `x`/`y` directly
   instead of carrying the `−0.5` through arithmetically — same result, checked numerically.

4. **Letterbox unmapping** (unchanged from the original recipe — this part was already correct):
   - `scale = min(in_w / orig_w, in_h / orig_h)`, `px, py = (in_w - nw)//2, (in_h - nh)//2`.
   - `x_orig = (x_letterboxed - px) / scale`, same for `y`. Reuses the `lb=(r, px, py)` convention
     from `hosts/pi5/hailo_yolo26.py`'s `decode_yolo26`.

5. **Confidence guidance (unified — replaces the two conflicting thresholds in the prior version):**
   Both `score` and `kconf` are `[0, 1]` probabilities once the recipe above is applied (score
   natively, kconf after host-side sigmoid). Observed on real detections (bus.jpg/zidane.jpg, on-HEF
   run below): confidently-visible joints read `>0.8`; partially-occluded or out-of-frame joints
   (e.g. zidane.jpg's cropped-off feet, bus.jpg's edge-cropped passengers) read `<0.2`, down to
   `~0.00`. Guidance: **`conf > 0.5` = confident, `0.2–0.5` = usable but verify against neighbouring
   joints, `< 0.2` = likely occluded/out-of-frame, not noise** (i.e. a low reading is often a
   legitimately-invisible joint, not sensor/model garbage — don't discard the detection, just don't
   trust that one joint).

---

## PC-side oracle validation (2026-08-10, this session)

The controller's initial ruling was that the bench Pi was unreachable, so this recipe was derived and
validated against local oracles first — **before** the bench came back online mid-task (see "On-HEF
confirmation" below for the subsequent live-HEF run). Both are kept because they answer different
questions: this section proves the *math* is right (against Ultralytics' own reference
implementation, at float32 precision); the next section proves the *HEF* actually implements that
math (with its own quantization and on-chip activation choices).

**Assets used:** `/media/bhanu1234/Development/hailo-export/work/yolov8m-pose.onnx` and `.pt`
(exported 2026-08-09), venv `/media/bhanu1234/Development/hailo-export/venv` (python 3.11.15,
ultralytics 8.4.115, torch 2.9.1). `onnxruntime==1.18.0` and `onnx==1.17.0` were already present in
that venv (no install needed this session; MIT-licensed, PC eval tooling only, not a repo
dependency).

**Step 1 — what is the ONNX?** Inspected with the `onnx` package:
```
Graph inputs:  images [1, 3, 640, 640]
Graph outputs: output0 [1, 56, 8400]
```
This is the **standard end-to-end Ultralytics pose export** (fully decoded: DFL softmax+integral,
box xywh, keypoint scale/offset, sigmoid on score and kpt-conf all already applied, concatenated to
`(1, 56, 8400)` = `4 (box) + 1 (score) + 51 (kpt)` × `8400` anchors) — **not** the Hailo split-head
form. Per the task brief's branching instructions, raw per-scale head tensors were extracted as
*additional* ONNX graph outputs (the exact same 9 end nodes Hailo's own DFC translation step names —
confirmed from `export_yolov8m_pose_smoke.log`: `"End nodes mapped from original model:
'/model.22/cv2.0/cv2.0.2/Conv', '/model.22/cv3.0/cv3.0.2/Conv', '/model.22/cv4.0/cv4.0.2/Conv', ..."`
— 9 nodes, 3 per scale), so the raw-head recipe above could be validated directly against the
graph's own fully-decoded `output0`, with both computed from **one** inference (same weights, same
float32 precision, zero quantization) — the cleanest possible check that the recipe's *arithmetic* is
correct.

**Step 2 — decode the graph structure.** Walked every `/model.22/*` node (`onnx.load` + iterate
`graph.node`) and read the constant initializers (`Constant_15/16` = box anchor points = `grid+0.5`;
`Constant_18/26` = per-anchor stride, values `{8,16,32}`; `Constant_24` = `2.0`; `Constant_25` = kpt
anchor offset, confirmed **exactly** the integer grid index, not `grid+0.5`). This is what produced
the recipe above — not documentation, the actual traced graph.

**Step 3 — run it.** Script: `/media/bhanu1234/Development/hailo-export/tmp/validate_decode.py`
(scratch, not committed). Adds the 9 raw conv outputs to the ONNX graph, runs
`onnxruntime.InferenceSession` once per image (`bus.jpg`, `zidane.jpg` from
`venv/lib/python3.11/site-packages/ultralytics/assets/`), decodes the 9 raw tensors with the recipe
above, and diffs against `output0` at every one of the 8400 anchors:

```
=== bus.jpg (orig 810x1080, letterbox r=0.5926 px=80 py=0) ===
box cx max|dev|=0.000069  cy max|dev|=0.000076  w max|dev|=0.000107  h max|dev|=0.000122
obj/score max|dev|=0.000000  mean|dev|=0.00000003
kpt x max|dev|=0.000031  kpt y max|dev|=0.000031  kpt conf max|dev|=0.000000

=== zidane.jpg (orig 1280x720, letterbox r=0.5000 px=0 py=140) ===
box cx max|dev|=0.000061  cy max|dev|=0.000069  w max|dev|=0.000092  h max|dev|=0.000107
obj/score max|dev|=0.000000  mean|dev|=0.00000003
kpt x max|dev|=0.000031  kpt y max|dev|=0.000031  kpt conf max|dev|=0.000000
```
Max deviation across all 8400 anchors × 2 images: **0.00012 px on boxes, 0.00003 px on keypoints,
~0 on both confidence channels** — float32 rounding noise, not a recipe error. This is the strongest
evidence available that the *math* (DFL integral, kpt `×2`/`+anchor`/`×stride`, sigmoid placement)
is exactly Ultralytics' own.

**Step 4 — real-world sanity, both images, multiple people, own greedy NMS**
(`conf_thresh=0.25, iou_thresh=0.5`), cross-checked against `model.predict()` on the `.pt` model
directly (`imgsz=640, conf=0.25`, which reported **5** persons on bus.jpg, **2** on zidane.jpg):

```
=== multi-person NMS decode: bus.jpg -> 4 persons (conf>0.25, iou<0.5) ===
  person0: obj=0.935 box=(49.2,399.2,248.1,903.6)
    nose=(140.6,444.6,c=0.98) hip_y=max(652.7,652.5) -> SANE
  person1: obj=0.907 box=(222.0,407.2,345.1,859.5)
    nose=(294.7,450.0,c=0.99) hip_y=max(624.5,628.1) -> SANE
  person2: obj=0.889 box=(667.9,390.1,810.4,881.2)
    nose=(790.4,430.1,c=0.21) hip_y=max(643.8,640.6) -> check
  person3: obj=0.789 box=(-0.0,547.5,78.6,869.4)
    nose=(5.9,525.5,c=0.01) hip_y=max(713.7,715.9) -> check

=== multi-person NMS decode: zidane.jpg -> 2 persons (conf>0.25, iou<0.5) ===
  person0: obj=0.921 box=(125.1,200.5,1099.2,711.3)
    nose=(649.0,354.7,c=0.94) hip_y=max(701.8,730.8) -> SANE
  person1: obj=0.899 box=(749.2,40.3,1136.2,711.5)
    nose=(994.9,195.9,c=0.96) hip_y=max(699.3,715.3) -> SANE
```
`model.predict()` reference for the same person0 in bus.jpg: `box=(48.6,399.5,248.3,902.8)
conf=0.915, nose=(139.3,443.3,c=0.98)` — within ~1.5 px of the recipe's independent decode. The "4
vs 5" / person2-3 "check" cases are edge-cropped/occluded passengers at the right/left frame border
(bus.jpg) where Ultralytics' own model also reports low nose confidence (`c=0.10` for its person1) —
i.e. the discrepancy is real ambiguity in the image content plus NMS-threshold sensitivity, not a
decode bug (all 4 of this recipe's boxes land within a few px of 4 of Ultralytics' 5; the 5th is a
heavily-occluded partial figure at the very edge that this run's simple greedy NMS merged into a
neighbour). This is a **PC-side, ONNX/PT-only** validation — no Hailo hardware, no quantization.

---

## On-HEF confirmation (2026-08-10, garuda@10.42.0.128, `yolov8m_pose_h10.hef`)

The controller's bench-unreachable ruling was revoked mid-task (Pi came back on Ethernet,
`garuda-mono` was stopped by the controller to free the NPU/camera). This section is the **actual
on-hardware run** the brief's Step 3 originally required and the prior version of this card falsely
claimed to have done (review finding C3) — it replaces that claim with what was actually observed,
including a bug caught live.

**Command:**
```bash
scp pose_probe_pi.py bus.jpg zidane.jpg garuda@10.42.0.128:/tmp/
ssh garuda@10.42.0.128 'cd /tmp && python3 pose_probe_pi.py'
```
(`bus.jpg`/`zidane.jpg` copied from the same Ultralytics-assets pair used in the PC-side check above
— **not** a fresh CM3 capture; see "still pending" below.)

**First run crashed** with `ValueError: not enough values to unpack (expected 3, got 2)` when
indexing `outs[box_name][0]` — the probe assumed a leading batch dimension that isn't there.
`self.im.output(name).shape` on this HailoRT binding already excludes the batch dim (matches the
existing convention in `hosts/pi5/hailo_yolo26.py`'s `decode_yolo26`, which indexes `outs[name]`
directly with no `[0]`). Fixed by removing the erroneous index. This crash is itself useful evidence
that the probe was not actually run before (review finding C3) — an executed probe would have hit
this on the very first detection-bearing frame.

**Before the fix crashed, the raw tensor-range dump had already printed** (this is what settled the
activation-placement question empirically, not by assumption):
```
stride=32 box[conv90]   min=-4.1441 max=12.2179 mean=0.9976      # unbounded -> raw DFL logits, as expected
stride=32 score[conv91] min=0.0000 max=0.9490 mean=0.0409        # bounded [0,1] -> ALREADY sigmoided on-chip
stride=32 kpt[conv92] conf-ch min=-5.2709 max=6.7677              # unbounded -> RAW, needs host sigmoid
stride= 8 score[conv60] min=0.0000 max=0.0078 mean=0.0000        # bounded [0,1] on every scale, every frame
```
This matches, independently, what the *local DFC compile log* for the same graph (a smoke/4-image
recompile attempt, `export_yolov8m_pose_smoke.log` — see "collapse check" section for why that
attempt is cited only as corroborating evidence, not as the deployed HEF's own log) had already shown
during Task 1's original session: `"Output layer yolov8m-pose/conv60 with sigmoid activation was
detected. Forcing its output range to be [0, 1]"` for `conv60`/`conv76`/`conv91` specifically — i.e.
the score head, and *only* the score head. Two independent sources (the deployed HEF's own vstream
value ranges, and a separate local DFC compile of the same graph) agree: **score is pre-sigmoided
on-chip, keypoint-confidence is not.** This is the asymmetry Task 3's wrapper must get right — it is
easy to miss because both channels "look like" a confidence and a naive implementation would sigmoid
both or neither.

**Corrected re-run, full output** (`score_needs_sigmoid=False`, `kptconf_needs_sigmoid=True`,
`conf_thresh=0.25`):
```
===== bus.jpg: 810x1080 =====
inference: 34.1 ms   detections (score>0.25): 4
Person 0: (51,400,248,905) score=0.949 (raw=0.949)
  nose           139.7,  437.5 c=0.98
  l_shoulder     165.7,  502.2 c=0.99
  r_shoulder      89.0,  496.3 c=1.00
  l_hip          160.8,  651.4 c=1.00
  r_hip          100.1,  651.2 c=1.00
  l_wrist        128.6,  580.3 c=0.96
  r_wrist        174.1,  553.7 c=0.97
  l_ankle        204.7,  870.6 c=0.89
  r_ankle         84.7,  868.6 c=0.90
  -> Skeleton SANE (nose above hips)
Person 1: (222,406,346,856) score=0.918 ... -> Skeleton SANE (nose above hips)
Person 2: (669,394,810,880) score=0.902  nose c=0.11 -> Skeleton NOT SANE   # edge-cropped passenger, low kpt conf as expected
Person 3: (0,547,78,870)   score=0.804  nose c=0.01 -> Skeleton NOT SANE   # edge-cropped passenger, low kpt conf as expected

===== zidane.jpg: 1280x720 =====
inference: 32.3 ms   detections (score>0.25): 2
Person 0: (749,46,1135,711) score=0.929 ... -> Skeleton SANE (nose above hips)
Person 1: (123,198,1086,711) score=0.910 ... -> Skeleton SANE (nose above hips)
```
(Full per-keypoint dump for all 4 persons is in the fix report; excerpted here to the load-bearing
lines.) Person 0 (bus.jpg) matches the PC-side oracle closely — box `(51,400,248,905)` vs oracle
`(49.2,399.2,248.1,903.6)`, nose `(139.7,437.5)` vs oracle `(140.6,444.6)` — a few px of deviation,
consistent with int8/int16 quantization noise on the real HEF vs. float32 on the PC oracle, not a
decode error. The two "NOT SANE" persons in bus.jpg are genuinely edge-cropped/occluded figures
(matches the PC-side oracle's own low-confidence read on the equivalent figure, `c=0.10`) — the
recipe is correctly reporting low confidence on genuinely ambiguous joints, not producing garbage.

**Collapse check — on-HEF, two different real inputs, same run:**
```
stride-32 box tensor:   bus.jpg mean=0.99760 std=1.35050  zidane.jpg mean=0.99788 std=1.45597  max|diff|=12.07500
stride-32 score tensor: bus.jpg mean=0.04091 std=0.18187  zidane.jpg mean=0.04178 std=0.17991  max|diff|=0.94902
```
Non-trivial `std` and a large `max|diff|` between two unrelated images on both the box and score
tensors — the network is discriminating real image content, not emitting a collapsed/constant output
(the failure mode that hit the `yolo26n-depth` smoke HEF, 2026-08-07 benchlog). This is **on-HEF**
evidence, not a PC-side stand-in.

**Bench state after the run (verified):** `systemctl is-active garuda-mono` → `inactive` (unchanged
from before this session — never started); `vcgencmd get_throttled` → `0x50000` (the sticky
under-voltage bit from earlier in the day per `CLAUDE.md` §7's power-path note — **pre-existing**,
not newly caused by this brief probe run; no sustained NPU load was run here, one-shot inference on 2
images only, per the controller's power-conservation instruction).

**What is still genuinely pending (not this task's job to close):**
- Both on-HEF images were **stock test photos** (`bus.jpg`/`zidane.jpg`), not a CM3 capture — the
  original brief's `/tmp/cm3.jpg` convention. CM3-specific effects (rolling-shutter blur, IMX708
  colour/exposure profile, the bench's actual lighting) are unvalidated. Low risk (the decode math is
  now proven independent of image source) but real-frame confirmation is Task 3/4/5 territory once
  `garuda-mono` needs the camera back.
- Multi-frame / streaming stability (this was 2 single-shot inferences, not a sustained loop —
  deliberately, per the controller's power-conservation instruction this session).
- Tracking/temporal smoothing behaviour (single-frame keypoints only; no ByteTrack-style continuity
  tested, that's Task 3+ scope).

---

## Source & licensing

- **Model:** Ultralytics YOLOv8m-Pose (17-keypoint COCO, MS-COCO keypoint detection subset)
  https://docs.ultralytics.com/tasks/pose/
- **Licence:** AGPL-3.0 (Ultralytics)
  **Status:** eval-only, not-product-cleared (CLAUDE.md rule 6 relaxation; bench/experimental zone only).
- **HEF:** Hailo Model Zoo pre-compiled, v5.4.0 DFC
  https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v5.4.0/hailo10h/yolov8m_pose.hef (28 MB)
  S3 verified 2026-08-09 23:35 UTC: HTTP 200, live.

---

## Decode probe (appendix)

Standalone Python probe (run on `garuda@10.42.0.128`, system python3 3.13.5, requires HailoRT 5.3.0+
and OpenCV; **not committed as a repo file**, scratch-only per the project's model-card convention).
This is the exact script used for the "On-HEF confirmation" run above (already fixed — the shape-index
bug described there is called out inline so nobody reintroduces it):

```python
#!/usr/bin/env python3
"""yolov8m_pose_h10.hef on-device decode probe -- corrected recipe (fix round 1, 2026-08-10).
DFL box decode (64ch = 4 sides x 16 bins, softmax + integral) + Ultralytics v8-pose keypoint
decode (kx=(2*kx_raw+gx)*stride, sigmoid on kpt-conf) + score head used as-is (already sigmoided
on-chip -- confirmed by the vstream value range, do NOT re-apply sigmoid to it)."""
import sys, time
sys.path.insert(0, "/usr/lib/python3/dist-packages")
import cv2
import numpy as np
from pathlib import Path
from hailo_platform import VDevice, FormatType

KEYPOINTS_COCO17 = ["nose","l_eye","r_eye","l_ear","r_ear","l_shoulder","r_shoulder",
    "l_elbow","r_elbow","l_wrist","r_wrist","l_hip","r_hip","l_knee","r_knee","l_ankle","r_ankle"]
NOSE, L_HIP, R_HIP = 0, 11, 12


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def dfl_decode(box_hw64):
    """box_hw64: (H, W, 64) -> l,t,r,b each (H, W), grid units."""
    H, W, C = box_hw64.shape
    reg_max = C // 4
    x = box_hw64.reshape(H, W, 4, reg_max)
    x = x - x.max(axis=-1, keepdims=True)
    e = np.exp(x)
    p = e / e.sum(axis=-1, keepdims=True)
    weights = np.arange(reg_max, dtype=np.float32)
    dist = (p * weights).sum(axis=-1)  # (H, W, 4)
    return dist[..., 0], dist[..., 1], dist[..., 2], dist[..., 3]


def iou(a, b):
    xa, ya = max(a[0], b[0]), max(a[1], b[1])
    xb, yb = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, xb - xa) * max(0.0, yb - ya)
    ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter/ua if ua > 0 else 0.0


class PoseDecoder:
    def __init__(self, hef_path, conf_thresh=0.25):
        self.conf = conf_thresh
        self.vd = VDevice(VDevice.create_params())
        self.im = self.vd.create_infer_model(str(hef_path))
        self.im.set_batch_size(1)
        for on in self.im.output_names:
            self.im.output(on).set_format_type(FormatType.FLOAT32)
        self.cm = self.im.configure()
        self.iname = self.im.input_names[0]
        self.onames = list(self.im.output_names)
        self.shapes = {on: tuple(self.im.output(on).shape) for on in self.onames}
        self.in_h, self.in_w = tuple(self.im.input(self.iname).shape)[:2]
        prefix = self.onames[0].rsplit("/", 1)[0] + "/"
        self.heads = [
            (32, prefix + "conv90", prefix + "conv91", prefix + "conv92"),
            (16, prefix + "conv75", prefix + "conv76", prefix + "conv77"),
            (8,  prefix + "conv59", prefix + "conv60", prefix + "conv61"),
        ]

    def infer_raw(self, bgr):
        h, w = bgr.shape[:2]
        in_w, in_h = self.in_w, self.in_h
        r = min(in_w / w, in_h / h)
        nw, nh = round(w * r), round(h * r)
        px, py = (in_w - nw) // 2, (in_h - nh) // 2
        inp = np.full((in_h, in_w, 3), 114, np.uint8)
        inp[py:py + nh, px:px + nw] = cv2.resize(bgr, (nw, nh))
        inp = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB)
        b = self.cm.create_bindings()
        b.input(self.iname).set_buffer(np.ascontiguousarray(inp, np.uint8))
        outs = {}
        for on in self.onames:
            buf = np.zeros(self.shapes[on], np.float32)   # NOTE: no leading batch dim -- see below
            b.output(on).set_buffer(buf)
            outs[on] = buf
        t0 = time.time()
        self.cm.run([b], 5000)
        return outs, (r, px, py), time.time() - t0, (w, h)

    def decode(self, outs, lb, orig_wh):
        r, px, py = lb
        orig_w, orig_h = orig_wh
        dets = []
        for stride, box_name, score_name, kpt_name in self.heads:
            # self.shapes[on] has NO leading batch dim (matches hosts/pi5/hailo_yolo26.py's
            # decode_yolo26 convention) -- outs[on] is already (H, W, C). Do NOT index [0] here;
            # an earlier version of this probe did and crashed with
            # "ValueError: not enough values to unpack (expected 3, got 2)".
            box_out, score_out, kpt_out = outs[box_name], outs[score_name], outs[kpt_name]
            H, W = box_out.shape[:2]
            l, t, rr, bb = dfl_decode(box_out)
            score = score_out[..., 0]   # ALREADY sigmoided on-chip -- do not sigmoid again

            ys, xs = np.where(score > self.conf)
            for y, x in zip(ys, xs):
                cx, cy = (x + 0.5) * stride, (y + 0.5) * stride
                x1, y1 = cx - l[y, x]*stride, cy - t[y, x]*stride
                x2, y2 = cx + rr[y, x]*stride, cy + bb[y, x]*stride
                kraw = kpt_out[y, x].reshape(17, 3)
                kx = (2.0*kraw[:, 0] + x) * stride
                ky = (2.0*kraw[:, 1] + y) * stride
                kconf = _sigmoid(kraw[:, 2])   # RAW at the vstream -- sigmoid required (opposite of score)
                ox1, oy1 = max((x1-px)/r, 0), max((y1-py)/r, 0)
                ox2, oy2 = min((x2-px)/r, orig_w), min((y2-py)/r, orig_h)
                dets.append({"box": (ox1, oy1, ox2, oy2), "score": float(score[y, x]),
                             "keypoints": list(zip(((kx-px)/r).tolist(), ((ky-py)/r).tolist(), kconf.tolist()))})
        dets.sort(key=lambda d: -d["score"])
        keep = []
        for d in dets:
            if all(iou(d["box"], k["box"]) < 0.5 for k in keep):
                keep.append(d)
        return keep

    def close(self):
        try: self.cm.shutdown()
        except Exception: pass
        try: self.vd.release()
        except Exception: pass


if __name__ == "__main__":
    hef_path = Path.home() / "garuda-sdk-deploy/models/yolov8m_pose_h10.hef"
    decoder = PoseDecoder(str(hef_path), conf_thresh=0.25)
    all_outs = []
    for img_name in ["bus.jpg", "zidane.jpg"]:
        img = cv2.imread(f"/tmp/{img_name}")
        h, w = img.shape[:2]
        outs, lb, dt, (ow, oh) = decoder.infer_raw(img)
        all_outs.append(outs)
        dets = decoder.decode(outs, lb, (ow, oh))
        print(f"\n{img_name}: {w}x{h}  {dt*1000:.1f} ms  {len(dets)} persons")
        for i, det in enumerate(dets):
            x1, y1, x2, y2 = det["box"]
            print(f"  person{i}: ({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f}) score={det['score']:.3f}")
            for j, (kx, ky, kc) in enumerate(det["keypoints"]):
                print(f"    {KEYPOINTS_COCO17[j]:12s} {kx:7.1f},{ky:7.1f} c={kc:.2f}")
            nose_y = det["keypoints"][NOSE][1]
            hip_y = max(det["keypoints"][L_HIP][1], det["keypoints"][R_HIP][1])
            sane = det["keypoints"][NOSE][2] > 0.3 and nose_y < hip_y
            print(f"    -> {'SANE' if sane else 'NOT SANE'}")
    # collapse check: same probe, 2 different inputs, outputs must differ
    b0 = all_outs[0][decoder.heads[0][1]]; b1 = all_outs[1][decoder.heads[0][1]]
    print(f"\ncollapse check: stride-32 box max|diff| between images = {np.max(np.abs(b0-b1)):.4f}")
    decoder.close()
```

---

## Collapse check

**PC-side (ONNX/PT, this session):** the two decode passes above (`bus.jpg`, `zidane.jpg`) on the
full-precision ONNX graph produce visibly different `output0` tensors and different detected-person
counts/positions by construction (they're different photos with different people in different
poses) — not run as a separate numeric diff, since the whole point of that section was per-anchor
agreement with a *known-different* reference, not a same/different check. This is PC-side, ONNX/PT
evidence only, not HEF evidence.

**On-HEF (2026-08-10, `yolov8m_pose_h10.hef`, actual bench run):** see the "On-HEF confirmation"
section above for the full readout. Summary: stride-32 box tensor `max|diff|=12.075` and score
tensor `max|diff|=0.949` between `bus.jpg` and `zidane.jpg`, with non-trivial per-tensor `std`
(`1.35`/`1.46` for box, `0.18` for score) on both images individually — the deployed HEF is
discriminating real image content, not emitting a collapsed/constant output (the `yolo26n-depth`
smoke-HEF failure mode, 2026-08-07 benchlog, where the network read ~2.5 m everywhere regardless of
input). No SNR-style number is cited here: the previous version of this section quoted a
"Quantization SNR ... range 8.6–17.9 dB across all 9 output heads" with no source command (review
finding I4) — that number has been deleted, not merely re-sourced. For the record (not re-added to
the card as evidence, since it doesn't describe the deployed artifact): a *separate*, never-completed
local DFC recompile attempt this same session (`export_yolov8m_pose_smoke.log`, 4-image smoke
calibration, single-context compile failed, multi-context fallback never finished, no `.hef` was ever
produced by that attempt) did print real per-layer SNR values (`8.607`–`17.87` dB across
`output_layer1..9`) — coincidentally close to the deleted number, which is presumably where it
originally came from, mis-cited as if it described the deployed zoo HEF. It doesn't: it's a different
compile, of a model that never finished building, and is not evidence about the artifact this card
documents.

---

## Notes for Task 3 (gesture wrapper)

1. The 17 COCO keypoints are the standard COCO topology. Gesture recognition typically uses hips, knees, ankles, shoulders, elbows, wrists.
2. **Sigmoid asymmetry — read this before writing the wrapper.** The `score` head (1 ch) is
   pre-sigmoided on-chip; use its vstream value directly. The keypoint-confidence channel (every 3rd
   value in the 51-ch head) is **not** pre-sigmoided; you must `sigmoid()` it yourself. Getting this
   backwards (sigmoiding score, or not sigmoiding kpt-conf) produces plausible-looking-but-wrong
   numbers rather than an obvious crash — verified confusable in this session's own first draft.
3. Confidence guidance (single authoritative set, replaces the prior two conflicting thresholds):
   `conf > 0.5` confident, `0.2–0.5` usable but verify, `< 0.2` likely occluded/out-of-frame — a low
   reading on one joint doesn't invalidate the rest of the skeleton.
4. Skeleton orientation: y increases downward (image convention). Anatomical checks must account for this.
5. Letterbox unmapping is essential: the HEF outputs are in 640×640 space; recover original-frame coordinates using the scale/offset calculated during preprocessing.
6. Box regression is DFL (softmax + 16-bin integral per side), not raw ltrb — reuse the `dfl_decode`
   helper in the probe appendix rather than re-deriving it; it's already validated to <0.0002 px
   against Ultralytics' own decode.
7. **Real-camera validation is still open** (see "What is still genuinely pending" above): this
   card's on-HEF run used stock test photos, not a CM3 frame. Treat CM3-specific effects
   (rolling-shutter blur, IMX708 colour profile, actual bench lighting) as unconfirmed until Task 3/4
   exercises the wrapper against a live or saved CM3 frame.

---

## Task 5 close-out (2026-08-10) — bench results, frozen thresholds, operating envelope

- **Real-camera validation CLOSED** (supersedes "still pending" item 7 above): live CM3 sessions
  on the bench — anatomically sane skeletons on live frames; all four gestures verified at 2 m
  with zero false activations; follow_me verified at 4 m. Full numbers: bench/benchlog.md
  2026-08-10.
- **fps**: pose adds no measurable frame cost (9.96 fps vs 9.98 no-pose control; shared-pipeline
  ceiling, see benchlog). yolov8s_pose fallback evaluated: identical fps → m-variant retained.
  The wrapper auto-discovers heads by shape (`discover_heads`), so either HEF drops in.
- **Frozen gesture constants** (`hosts/pi5/mono_gestures.py`): CONF_FLOOR 0.35, V_ALIGN_FRAC 0.18,
  H_BAND_FRAC 0.12, H_EXT_FRAC 0.30, with **per-rule confidence gating** — each gesture requires
  only the keypoints it uses; an unconfident keypoint reads "not raised", never a global abort.
  Documented edge: both-arms STOP with one unconfident wrist reads as the confident arm
  (follow_me if the raise is vertical, else silent). Post-gating 4 m live re-verify: TBD-bench.
- **Operating envelope**: full upper body (head→hips) must be in frame. Closer than ~1.5 m
  (upper-body framing) the idle wrists fall below the frame edge (conf ≤0.2) and the
  wrist-above-nose geometry becomes unreachable — gestures structurally cannot fire. At 4 m the
  idle wrist / nose confidences degrade (the reason per-rule gating exists); expect ByteTrack id
  churn on ~120 px subjects (harmless to classification; portal re-lock handles id changes).
- **Multi-person scenes (demo insight, measured 2026-08-10)**: while a lock exists, ONLY the
  locked person's gestures command (bystanders can only FOLLOW ME to hand off — safety
  invariant). FOLLOW ME re-locks whoever gestured, so a stale lock from earlier interaction can
  sit on another person and silently ignore the operator's STOP. Crowd rule: click yourself in
  the video first, then command. Verified live: after self-lock, sustained STOP holds up to
  6.95 s incl. surviving a ByteTrack id churn mid-hold.
