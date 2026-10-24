# Pose + gesture control — design spec

Date: 2026-08-09 · Status: **implemented 2026-08-10** (branch `feat/pose-gestures`, stacked on
`feat/portal-tracking`; bench: benchlog 2026-08-10). Implementation deviations from this spec:
zoo `yolov8m_pose` HEF instead of yolo26m-pose (§1 fallback ladder — exporter hard-gate);
gesture HUD is the chip only — §3's held-progress element was dropped at plan level (open minor,
revisit if wanted); classify_pose uses per-rule confidence gating (owner-ruled 2026-08-10, bench
finding); post-gating 4 m live re-verify TBD-bench.

## Problem

The Tracking demo follows a clicked person but reads as passive. The owner wants the "palm
control" experience: the tracked person commands the (virtual) drone with body gestures, visibly —
skeleton overlay, recognized-gesture HUD, and follow behavior that reacts. Bbox-center locking is
also jumpy when limbs move; keypoints fix that for free.

## Decision (owner-approved)

**Straight to gestures** (tiers below are staged tasks, but the plan is judged by gestures
working): (1) skeleton overlay, (2) chest-point lock, (3) a **minimal 3-gesture vocabulary** —
STOP, FOLLOW ME, POINT — each held ~1 s to trigger, with HUD feedback. Segmentation is explicitly
deferred to its own follow-on spec (it will reuse the detector-mode pattern this work extends).

## 1. Model path (spike-gated)

Export **`yolo26m-pose`** on the PC via the proven PTQ pipeline (level 2, calib set as per the
depth/960² builds; Ultralytics **AGPL-3.0 → eval-only, not-product-cleared**, models/ zone).
**SPIKE gates the plan** (world-mode pattern): `hailortcli parse-hef` the pose HEF, decode the
pose head standalone on a saved frame, record the exact output tensor contract in a model card
(`models/yolo26m_pose_h10.md`) before any API is built on it. Fallback ladder, in order:
`yolo26s-pose` (if `m` fails the bench fps gate), then a zoo pre-compiled pose HEF (if the
26-pose head won't decode; licence per zoo card, eval-only either way). No fps figures are claimed
in advance — the bench gate (below) decides; rule 1 applies.

## 2. garuda-mono changes

- **Third detector mode:** `POST /api/detector?mode=primary|world|pose` — same switch pattern and
  API world mode uses. Pose models emit person boxes, so ByteTrack, click-to-lock,
  coast/re-acquire, and depth→distance work unchanged downstream.
- **Wire snapshot v2** (`mono_state.py`, versioned `v: 2`): tracks gain optional
  `kpts: [[x, y, conf] × 17]` in **source-frame pixel coordinates — the same convention as the
  sibling track bbox fields** (one payload, one convention; the DetectionSet lesson lives at the
  portal boundary, which already normalizes bboxes by the snapshot's own `frame` dims and does the
  same for keypoints); top-level `gesture: {name: "stop"|"follow_me"|"point_left"|
  "point_right"|null, track_id, held_ms}`. Consumers on v1 keys are unaffected (additive change;
  `v` bumps because the contract grows).
- **Gesture classifier in mono** (pure module, e.g. `hosts/pi5/mono_gestures.py`): geometry on
  keypoint arrays only — no cv2/hailo imports, unit-tested on x86 CI:
  - **STOP** — both wrists above the nose (`wrist.y < nose.y`, both arms).
  - **FOLLOW ME** — exactly one wrist above the nose with that arm near-vertical
    (`|wrist.x − shoulder.x|` below a width fraction).
  - **POINT** — wrist within a height band of the shoulder and extended sideways
    (`|wrist.x − shoulder.x|` above a width fraction); direction from the sign.
  - Robustness: per-keypoint confidence floor; trigger only after ≥1 s continuous hold
    (time-based, not frame-count — fps varies); release hysteresis (gesture clears only after a
    shorter continuous absence) so 15 fps flicker can't chatter the state. Exact thresholds are
    constants in the module, tuned once on the bench and recorded in the model card.
  - Only the **locked** track's gestures are classified when a lock exists; otherwise the largest
    person (FOLLOW ME must work while unlocked — that's its purpose).

## 3. Portal changes

- **Skeleton overlay:** the follow overlay draws bones/joints from `kpts` in the existing `mono`
  SSE events on the existing canvas (same normalized-coords contract as boxes).
- **Gesture → meaning (portal-side; mono stays pure perception):**
  - STOP → pause the follow pipeline (drone-dir zeroes), HUD banner "STOP (gesture)". Cleared by
    FOLLOW ME or by clicking the video.
  - FOLLOW ME → lock the gesturing person: portal calls mono's existing lock API with that
    track's id; follow resumes if paused.
  - POINT → HUD direction chip only (`◀ point` / `point ▶`), no motion semantics yet.
- HUD additions live in the Tracking sidebar (recognized-gesture chip + held-progress); element
  ids additive only — the cockpit structural test grows, nothing renamed.

## 4. Failure modes

- Pose HEF collapse or undecodable head → spike fails the plan early, fallback ladder engages;
  nothing lands half-wired (world-mode precedent).
- Low-confidence keypoints (back turned, occlusion) → confidence floor keeps the classifier
  silent; no gesture is better than a false STOP.
- Gesture from a non-locked bystander → ignored except FOLLOW ME (by design, that's the handoff
  gesture); if two people gesture FOLLOW ME simultaneously, largest bbox wins (documented,
  deterministic).
- Mode switched away from pose → `kpts`/`gesture` absent from snapshots; portal HUD hides
  gesture UI (presence-keyed, no errors).

## 5. Power & bench gates

Implementation and CI proceed now; **every sustained bench run waits for the 27 W PSU** (or runs
in short windows via the Tracking-tab mono button). Ship gate, measured → benchlog (rule 1): pose
mode sustains camera fps with depth co-resident, `get_throttled` low bits `0x0`, on a clean rail;
gesture trigger/release verified live by the owner. `yolo26m-pose` failing the fps floor →
`yolo26s-pose` rebuild, same gate.

## 6. Security

No new surfaces: the detector-mode POST and SSE feed already exist; gestures add no network-
reachable control beyond the existing unauthenticated portal/mono exposure (pre-Phase-8 auth gate
unchanged). Nothing here touches the FC path (rule 12) — all gesture meaning terminates in the
portal's intent layer.

## 7. Testing

- x86 CI: `mono_gestures` unit tests with synthetic keypoint fixtures per gesture — trigger,
  hold-time, confidence-floor, hysteresis, and two-person FOLLOW ME arbitration; snapshot v2
  builder tests; portal adapter/HUD structural tests extend the existing suites.
- Pi spike: parse-hef + standalone decode on a saved frame (model card records the contract).
- Bench close-out: owner runs the 3-gesture demo end-to-end over the hotspot; fps/thermals
  recorded per §5.

## 8. Licences

`yolo26m/s-pose`: Ultralytics AGPL-3.0 — bench/eval only, not-product-cleared, models/ zone
(existing 2026-08-02 owner relaxation). Zoo fallback HEF: per its zoo card, eval-only. No new
runtime dependencies expected; any new dep → licence note in the commit (rule 6).

## 9. Sequencing

1. Spec review (this document) → implementation plan via writing-plans.
2. Plan shape (indicative): T1 spike (export + parse + decode + model card) → T2 mono_gestures
   pure module (TDD) → T3 mono pose mode + snapshot v2 → T4 portal overlay + gesture semantics →
   T5 bench gate + docs close-out. Small tested commits (rule 7).
3. Bench-gated steps schedule around the PSU arrival; everything x86 lands independently.

## Open questions

- Pose head output contract on the 10H (spike resolves; determines decode code and whether
  `yolo26*-pose` PTQs cleanly like detect/depth did).
- Threshold constants (arm-angle fractions, hold/release ms) — set once on-bench during T5, then
  frozen in the module + model card.
