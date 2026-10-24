# Portal tracking convergence + detection upgrades — design spec

Date: 2026-08-07 · Status: implemented 2026-08-07 · Branch: `feat/portal-tracking`
(worktree `.claude/worktrees/feat+portal-tracking`), through Task 9 close-out. Merge to master:
owner gate, not yet merged.

## Problem

The bench has two tracking UIs: the portal's follow view (:8080, click-to-lock reticle + drone-dir
HUD, fed by a spawned `garuda-inference` child) and the garuda-mono dev GUI (:8098, YOLO26m +
ByteTrack lock + y26d metric distance + cal). The portal only embeds mono as an iframe tab. Three
single-client CM3 consumers exist (portal CameraStream, garuda-inference child, garuda-mono), so
camera ownership conflicts are structural. Separately, detection quality gaps: far/small objects
missed (640² input on 1280×720 frames) and vocabulary gaps (pen, keys — not in COCO's 80).

Detector survey (zoo published numbers, COCO mAP float/quantized): current yolo26m 52.3/51.0 is
already near the top for hailo10h — only yolov10x (53.7/52.1) and yolov11x (54.1/53.1) beat it, at
2–3× the compute. A closed-set model swap is NOT the fix. The zoo does ship `yolo_world_v2s`
(zero-shot/open-vocab, 640², 46.6 fps b1, mAP 36.4/31.9, pre-compiled HEF) — that covers arbitrary
classes via text prompts.

## Decision (owner-approved)

- **A: mono stays the perception service; the portal becomes a first-class client.**
- **Converge:** the follow view UI becomes THE portal tracking tab, backed by mono's track feed.
- Detection: keep yolo26m as primary; add `yolo_world_v2s` open-vocab mode; attempt a 960²
  yolo26m PTQ rebuild for far/small objects, gated on bench numbers.

## 1. garuda-mono API (:8098)

Existing (unchanged): MJPEG `/stream`; POST `/api/mono_click`, `/api/mono_target`,
`/api/mono_calibrate`, `/api/mono_calibrate_reset`; compute idles with no stream viewer.

New:
- `GET /api/events` — server-sent events, one JSON state object per processed frame (throttled to
  ≤10 Hz): `{v, age_ms, tracks: [{id, label, conf, x, y, w, h, coasting}], lock: {id, class, coasting}, distance: {raw_m, est_m, valid}, depth_engine, detector: {mode, prompt}, cal: {n, fit}, frame: {width, height}}`. `GET /api/state`
  returns the same object once (poll fallback).
- `POST /api/depth_engine?engine=y26d|da` — live switch; both engines stay configured on
  the NPU (10H has 8 GB on-module). The systemd drop-in `GARUDA_MONO_DEPTH` remains the boot
  default only.
- `POST /api/detector?mode=primary|world&prompt=pen&prompt=keys...` — world mode
  computes CLIP text embeddings for the prompt (Pi CPU) and swaps the detect path to
  `yolo_world_v2s.hef`; ByteTrack + lock logic downstream is identical for both detectors.

Track/lock/coast/re-acquire behaviour is owner-verified and does not change.

## 2. Detection upgrades (inside mono)

- **Open-vocab:** `yolo_world_v2s.hef` from the zoo (pre-compiled hailo10h, v5.4.0). Prompt →
  CLIP ViT-B/32 text encoder on Pi CPU → class embeddings → detector. **SPIKE (plan task 1):**
  verify the HEF's embedding input contract with `hailortcli parse-hef` + hailo-apps reference
  code before building the API around it. Licence GPL-3.0 → EVAL-ONLY (models/ card + LICENCES.md
  row, HEF git-ignored, never wired into `services/`).
- **Far/small objects:** rebuild yolo26m detect at 960×960 via the proven PC PTQ pipeline
  (level 2, 1024-img calib, `models/build_yolo26_depth_hef.sh` pattern). Ship gate (measured →
  benchlog): sustains camera fps (15) with depth co-resident, `get_throttled` low bits 0x0.
  Fails gate → stay at 640².

## 3. Portal tracking view (converged)

- The follow overlay (canvas reticle, click-to-lock, drone-dir HUD) becomes the single "Tracking"
  tab. Its detection source changes from the `garuda-inference` child to mono's SSE feed.
- Portal-side proxy: `portal.py` subscribes to mono `/api/events`, adapts mono `tracks` to the
  follow detection dict schema (`class_id, label, confidence, x, y, w, h` — same per-detection
  drop-guard as `follow_pipeline`), and re-emits on the portal bus. `FollowPipeline`/garuda-follow
  modules are untouched; drone-dir keeps working.
- HUD additions: distance readout + 60 s sparkline, cal add-point/reset buttons, depth-engine
  toggle, class dropdown (COCO) + free-text prompt box (world mode). Click-lock and class/prompt
  POSTs call mono's API directly (browser → :8098 via CORS *), not through the portal.
- Removed: the "mono" iframe tab. Retired: `InferenceManager`'s CM3 child path (mono is the sole
  CM3 holder). The :8098 GUI survives as a debug page.

## 4. Data flow & failure modes

CM3 → mono (YOLO26 every frame → ByteTrack → depth every Nth) → SSE + MJPEG → portal proxy →
browser overlay + FollowPipeline → drone-dir HUD.

- Mono down → tracking tab offline banner + auto-retry; portal stays up.
- Malformed track event → that detection dropped, frame continues (existing guard pattern).
- Prompt/embedding failure → fall back to primary detector, visible notice.
- Engine-switch failure → stay on current engine, surface error.
- Compute idle rule unchanged: no viewer/subscriber → mono idles (SSE subscribers count as
  viewers).

## 5. Security (pre-Phase-8 gate)

Both the portal (:8080) and mono API (:8098) currently run unauthenticated (CORS allow *). Before Phase 8, both services must be closed behind an auth gate (portal owns the gate; mono auth delegates to it).

## 6. Testing

- Unit (x86 CI): schema adapter, new mono endpoint handlers, portal proxy against a fake mono SSE
  fixture. Hardware-dependent tests `@requires_hw` so CI skips cleanly.
- On-bench (all numbers measured → benchlog, rule 1): world-mode fps + thermals; 960² gate run;
  end-to-end lock→distance sanity after convergence.

## 7. Licences

- yolo_world_v2s: GPL-3.0 — eval-only zone (`models/`), never in `services/`. CI licence-scan
  unaffected (models/ excluded).
- yolo26m 960² rebuild: Ultralytics AGPL-3.0 — bench/eval, not-product-cleared (existing owner
  relaxation 2026-08-02).
- Portal/mono code changes: project licence, no new deps expected. Any new dep → licence stated in
  PR + bitbake stub (rule 6).

## 8. Sequencing

1. `feat/stereo-target-range` merges first (gated on owner move-test + tape-measure run).
2. New branch `feat/portal-tracking` (worktree): spike (world HEF embedding contract) → mono API →
   portal proxy + converged view → detection upgrades (world mode, then 960² gate run).
3. Implementation plan via writing-plans skill; tasks land as small tested commits (rule 7).

## Open questions

- World-HEF embedding input contract (spike resolves; if the HEF turns out fixed-vocab-compiled,
  fall back: zoo retraining flow or prompt-set-at-build-time with a rebuild path documented).
- SSE vs long-poll on the Pi's Python http.server threading model — spike decides transport
  details (SSE preferred).
