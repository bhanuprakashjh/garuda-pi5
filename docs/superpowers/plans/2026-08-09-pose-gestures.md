# Pose + Gesture Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The tracked person commands the virtual drone with body gestures — skeleton overlay on the Tracking tab, STOP / FOLLOW ME / POINT recognized from a zoo `yolov8m_pose` HEF (amended from `yolo26m-pose` 2026-08-09: Ultralytics Hailo exporter hard-gates all YOLO26 pose models; spec §1 fallback ladder engaged), follow behavior reacting live.

**Architecture:** Pose becomes garuda-mono's third detector mode (`primary | world | pose`, same lazy-switch pattern world mode uses); pose person-boxes feed the unchanged ByteTrack/lock/depth path while keypoints ride the tracks into wire-snapshot v2. A pure gesture classifier + hold/release FSM lives in `hosts/pi5/mono_gestures.py` (x86-tested). The portal draws skeletons from the existing SSE feed and maps gestures to meaning server-side: STOP → `FollowPipeline.paused`, FOLLOW ME → lock the gesturing person via mono's existing click API, POINT → HUD only.

**Tech Stack:** Hailo model-zoo pre-compiled HEF (yolov8m_pose, DFC 5.4.0; own-export path retired — exporter gate), HailoRT 5.3.0 python API, Python stdlib http.server, vanilla JS, pytest.

**Spec:** `docs/superpowers/specs/2026-08-09-pose-gesture-control-design.md`

## Global Constraints

- Branch: `feat/pose-gestures`, worktree, **based on `feat/portal-tracking`** (not master — mono/portal code this builds on lives only there). Create at execution start via superpowers:using-git-worktrees. Rebase onto master only if the portal-tracking merge (owner-gated on the 27 W PSU) lands first.
- Coordinate convention: snapshot `kpts` are **source-frame pixels**, same as sibling track bboxes; the portal normalizes at its boundary (spec §2, amended 2026-08-09). Never emit mixed conventions in one payload.
- Rule 1: no invented numbers — fps/thermal figures only from bench runs on a clean rail, else `TBD-bench`. Threshold constants are tuned once on-bench in Task 5, then frozen in code + model card.
- Power: sustained bench runs wait for the 27 W PSU; short verification windows use the Tracking-tab mono button. Any step that reboots or leaves mono running ends with mono STOPPED.
- Licences: `yolov8m/s_pose` (zoo pre-compiled, Ultralytics weights) AGPL-3.0 — eval-only, models/ zone, not-product-cleared. No new pip/apt deps without a licence note in the commit.
- COCO-17 keypoint indices (used throughout): 0 nose, 1/2 eyes, 3/4 ears, 5/6 shoulders, 7/8 elbows, 9/10 wrists, 11/12 hips, 13/14 knees, 15/16 ankles. Left = odd-first of each pair (5,7,9,...), right = even. Image y grows DOWN ("above" means smaller y).
- Pi deploy = `scp` to `garuda@10.42.0.128:~/garuda-sdk-deploy/<same path>` + `sudo systemctl restart garuda-mono` (portal files → `/opt/garuda/garuda-portal/` + restart `garuda-portal`). Never edit on the Pi.
- Known flake: 2 timing tests in the portal's camera-lazy-acquire suite; rerun in isolation before suspecting a change.

---

### Task 1: Spike — export yolo26m-pose, parse, decode probe, model card

> **DONE 2026-08-09, deviated per spec §1 fallback (controller-sanctioned):** the Ultralytics
> Hailo exporter hard-gates ALL YOLO26 pose/seg/OBB (`ValueError`, exporter ~line 629), so the
> export steps below were replaced by fetching the zoo pre-compiled `yolov8m_pose` HEF
> (hailo10h, DFC 5.4.0). Deliverables: card `models/yolov8m_pose_h10.md` (I/O contract + decode
> recipe), deployed HEF `~/garuda-sdk-deploy/models/yolov8m_pose_h10.hef`, LICENCES row —
> commit `bfb4ee5`. Steps below retained for the record; Tasks 2–5 read the amended names.

**Files:**
- Create: `models/yolo26m_pose_h10.md`
- Modify: `models/LICENCES.md` (one row)

**Interfaces:**
- Produces: the pose HEF deployed to the Pi (`~/garuda-sdk-deploy/models/yolo26m-pose.hef`) and a model card documenting the EXACT output tensor names/shapes/order and the keypoint decode recipe. Task 3's wrapper is written against this card. If the head cannot be decoded, the card records it and the fallback ladder (spec §1) engages — STOP the plan and report to the owner before Task 3.

- [ ] **Step 1: Export on the PC** (the existing build script is model-generic despite its name):

```bash
/media/bhanu1234/Development/GARUDASDK/models/build_yolo26_depth_hef.sh \
  /media/bhanu1234/Development/hailo-export yolo26m-pose \
  /media/bhanu1234/Development/hailo-export/datasets/depth-calib.yaml
```

(Calibration reads images only, so the depth calib set serves; if the pose loader rejects the yaml, drop the third arg — the exporter auto-downloads a small set — and note "smoke-calib only, production calib TBD" in the card.) Expected artifact: `.../work/yolo26m-pose_hailo_model/yolo26m-pose.hef`.

- [ ] **Step 2: Deploy + parse on the Pi**

```bash
scp /media/bhanu1234/Development/hailo-export/work/yolo26m-pose_hailo_model/yolo26m-pose.hef \
    garuda@10.42.0.128:~/garuda-sdk-deploy/models/
ssh garuda@10.42.0.128 'hailortcli parse-hef ~/garuda-sdk-deploy/models/yolo26m-pose.hef'
```

Record the verbatim output in the card: input shape, every output tensor name/shape/dtype. Expect box outputs like the detect HEF plus keypoint tensor(s) (shape hints: ×51 = 17×3 flattened, or a separate 17×3 head per scale).

- [ ] **Step 3: Standalone decode probe.** Write a throwaway probe (paste into the card as an appendix, do not commit as code) that runs one saved frame (`/tmp/cm3.jpg`, capture via mono's still if absent) through the HEF using the `HailoYolo26.__init__` pattern (FLOAT32 outputs, letterbox, bindings) and prints, per person: box + 17 keypoints with confidences. Iterate until the printed skeleton is anatomically sane on the test image (nose above hips, wrists at arm ends). Record in the card: the exact tensor→keypoint mapping, scale handling, and letterbox un-mapping (reuse of `lb=(r, px, py)` semantics from `decode_yolo26`).

- [ ] **Step 4: Model card + licence row.** `models/yolo26m_pose_h10.md` following `yolo_world_v2s_h10.md`'s structure: AGPL-3.0 banner, export command used, parse-hef block, decode recipe (Step 3 findings), probe appendix, and a "collapse check" note (probe output varies with input — the y26d smoke-HEF lesson). `models/LICENCES.md`: `| yolo26m-pose | pose keypoints | AGPL-3.0 | ❌ eval-only | yolo26m_pose_h10.md | 17-kpt COCO, gesture demo |`

- [ ] **Step 5: Commit** — `git add models/yolo26m_pose_h10.md models/LICENCES.md && git commit -m "docs(models): yolo26m-pose spike — export, I/O contract, decode recipe (AGPL eval-only)"`

---

### Task 2: mono_gestures — pure classifier + hold/release FSM (x86 TDD)

**Files:**
- Create: `hosts/pi5/mono_gestures.py`
- Test: `hosts/pi5/test_mono_gestures.py`

**Interfaces:**
- Produces: `classify_pose(kpts, box_w, box_h, conf_floor=0.35) -> str|None` (instantaneous: "stop" | "follow_me" | "point_left" | "point_right" | None; kpts = 17×[x,y,conf] pixels) and `GestureFSM(hold_ms=1000, release_ms=400, clock=time.monotonic)` with `.update(name: str|None) -> dict` returning `{"name": str|None, "held_ms": int}` — `name` is non-None only after `hold_ms` of continuous identical classification, and clears only after `release_ms` of continuous absence/change. Task 3 wires these; Task 5 tunes the constants.

- [ ] **Step 1: Write the failing tests**

`hosts/pi5/test_mono_gestures.py`:

```python
from mono_gestures import classify_pose, GestureFSM

# Synthetic 17-kpt skeleton helper: a standing person in a 200x400 px box at origin.
# Indices: 0 nose, 5/6 shoulders, 9/10 wrists (others filled but unused by the rules).
def person(nose=(100, 50), lsho=(60, 120), rsho=(140, 120),
           lwri=(40, 200), rwri=(160, 200), conf=0.9):
    k = [[100.0, 250.0, conf]] * 17
    k[0] = [*map(float, nose), conf]
    k[5] = [*map(float, lsho), conf]; k[6] = [*map(float, rsho), conf]
    k[9] = [*map(float, lwri), conf]; k[10] = [*map(float, rwri), conf]
    return k

BOX = dict(box_w=200.0, box_h=400.0)

def test_neutral_pose_is_none():
    assert classify_pose(person(), **BOX) is None

def test_stop_both_wrists_above_nose():
    k = person(lwri=(60, 20), rwri=(140, 20))
    assert classify_pose(k, **BOX) == "stop"

def test_follow_me_one_arm_straight_up():
    k = person(lwri=(62, 20))                      # left wrist above nose, near shoulder x
    assert classify_pose(k, **BOX) == "follow_me"

def test_raised_but_sideways_arm_is_not_follow_me():
    k = person(lwri=(10, 20))                      # above nose but far off shoulder x
    assert classify_pose(k, **BOX) is None

def test_point_right_horizontal_extension():
    k = person(rwri=(200, 122))                    # at shoulder height, extended right
    assert classify_pose(k, **BOX) == "point_right"

def test_point_left():
    k = person(lwri=(0, 118))
    assert classify_pose(k, **BOX) == "point_left"

def test_low_confidence_keypoints_silence_the_classifier():
    k = person(lwri=(60, 20), rwri=(140, 20), conf=0.2)
    assert classify_pose(k, **BOX) is None

def test_fsm_requires_hold_and_release():
    t = {"now": 0.0}
    fsm = GestureFSM(hold_ms=1000, release_ms=400, clock=lambda: t["now"])
    assert fsm.update("stop")["name"] is None            # onset, not held yet
    t["now"] = 0.5
    assert fsm.update("stop")["name"] is None
    t["now"] = 1.1
    g = fsm.update("stop"); assert g["name"] == "stop" and g["held_ms"] >= 1000
    t["now"] = 1.2
    assert fsm.update(None)["name"] == "stop"            # brief dropout: held (hysteresis)
    t["now"] = 1.7
    assert fsm.update(None)["name"] is None              # released after release_ms

def test_fsm_changing_gesture_restarts_hold():
    t = {"now": 0.0}
    fsm = GestureFSM(hold_ms=1000, release_ms=400, clock=lambda: t["now"])
    fsm.update("stop"); t["now"] = 0.9; fsm.update("follow_me")
    t["now"] = 1.5
    assert fsm.update("follow_me")["name"] is None       # only 0.6 s of follow_me
    t["now"] = 2.0
    assert fsm.update("follow_me")["name"] == "follow_me"
```

- [ ] **Step 2: Run to verify failure** — `cd hosts/pi5 && python3 -m pytest test_mono_gestures.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

`hosts/pi5/mono_gestures.py`:

```python
"""Pure gesture rules on COCO-17 keypoints (pixel coords). No hailo/cv2 imports — x86 CI-tested.

Constants are bench-tuned once (plan Task 5) then frozen here + in models/yolov8m_pose_h10.md.
Image y grows down: "above" = smaller y.
"""
import time

NOSE, LSHO, RSHO, LWRI, RWRI = 0, 5, 6, 9, 10
CONF_FLOOR = 0.35
V_ALIGN_FRAC = 0.18    # |wrist.x - shoulder.x| <= frac*box_w  -> arm counts as vertical
H_BAND_FRAC = 0.12     # |wrist.y - shoulder.y| <= frac*box_h  -> wrist at shoulder height
H_EXT_FRAC = 0.35      # |wrist.x - shoulder.x| >= frac*box_w  -> arm counts as extended


def classify_pose(kpts, box_w, box_h, conf_floor=CONF_FLOOR):
    def ok(i): return kpts[i][2] >= conf_floor
    if not (ok(NOSE) and ok(LSHO) and ok(RSHO) and ok(LWRI) and ok(RWRI)):
        return None
    nose_y = kpts[NOSE][1]
    l_up = kpts[LWRI][1] < nose_y and abs(kpts[LWRI][0] - kpts[LSHO][0]) <= V_ALIGN_FRAC * box_w
    r_up = kpts[RWRI][1] < nose_y and abs(kpts[RWRI][0] - kpts[RSHO][0]) <= V_ALIGN_FRAC * box_w
    if kpts[LWRI][1] < nose_y and kpts[RWRI][1] < nose_y:
        return "stop"                                    # both raised (alignment not required)
    if l_up != r_up and (l_up or r_up):
        return "follow_me"
    for wri, sho, name in ((LWRI, LSHO, "point_left"), (RWRI, RSHO, "point_right")):
        if (abs(kpts[wri][1] - kpts[sho][1]) <= H_BAND_FRAC * box_h
                and abs(kpts[wri][0] - kpts[sho][0]) >= H_EXT_FRAC * box_w):
            return name
    return None


class GestureFSM:
    """Debounce: emit a gesture only after hold_ms of continuous identical classification;
    keep emitting through dropouts shorter than release_ms (15 fps flicker must not chatter)."""

    def __init__(self, hold_ms=1000, release_ms=400, clock=time.monotonic):
        self.hold_ms, self.release_ms, self.clock = hold_ms, release_ms, clock
        self._cand = None; self._cand_t = 0.0
        self._active = None; self._last_seen_t = 0.0

    def update(self, name):
        now = self.clock()
        if name is not None:
            if name != self._cand:
                self._cand, self._cand_t = name, now
            if (now - self._cand_t) * 1000 >= self.hold_ms:
                self._active = name
            if name == self._active:
                self._last_seen_t = now
        if self._active is not None and name != self._active:
            if (now - self._last_seen_t) * 1000 >= self.release_ms:
                self._active = None
        held = int((now - self._cand_t) * 1000) if self._active else 0
        return {"name": self._active, "held_ms": held}
```

- [ ] **Step 4: Run to verify pass** — same command → 10 PASS.

- [ ] **Step 5: Commit** — `git add hosts/pi5/mono_gestures.py hosts/pi5/test_mono_gestures.py && git commit -m "feat(mono): pure gesture classifier + hold/release FSM (x86 TDD)"`

---

### Task 3: mono pose mode — wrapper, kpts through tracking, snapshot v2

**Files:**
- Create: `hosts/pi5/hailo_yolo_pose.py` (family-neutral name — amended 2026-08-09)
- Modify: `hosts/pi5/mono_state.py` (v2), `hosts/pi5/garuda_mono.py` (pose mode + gesture wiring)
- Test: `hosts/pi5/test_mono_state.py` (extend)

**Interfaces:**
- Consumes: Task 1's model card (decode contract), Task 2's `classify_pose`/`GestureFSM`.
- Produces: `HailoYoloPose(hef, vdevice=None, conf_thresh=0.3)` with `.infer(bgr) -> [Det]` where each Det has `.x .y .w .h .label ("person") .conf .kpts` (17×[x,y,conf] source pixels); `POST /api/detector?mode=pose` (lazy HEF load, failure → `{"ok": false, "reason": ...}`, primary keeps running); snapshot v2: tracks may carry `"kpts"`, top-level `"gesture": {"name", "track_id", "held_ms"}`; `attach_kpts(tracks_full, dets) -> list` (9-tuples) pure helper in `mono_state.py`. Task 4 consumes exactly these keys.

- [ ] **Step 1: Extend mono_state (TDD).** Add to `hosts/pi5/test_mono_state.py`:

```python
def test_v2_tracks_carry_kpts_and_gesture():
    kp = [[1.0, 2.0, 0.9]] * 17
    st = {**STATE, "tracks_full": [(3, "person", 10.0, 20.0, 30.0, 40.0, 0.9, False, kp)]}
    s = build_snapshot(st, {**EXTRAS, "gesture": {"name": "stop", "track_id": 3, "held_ms": 1200}})
    assert s["v"] == 2
    assert s["tracks"][0]["kpts"] == kp
    assert s["gesture"] == {"name": "stop", "track_id": 3, "held_ms": 1200}

def test_v2_kpts_absent_when_not_pose_mode():
    s = build_snapshot(STATE, EXTRAS)          # 8-tuples, no gesture extra
    assert "kpts" not in s["tracks"][0]
    assert s["gesture"] == {"name": None, "track_id": None, "held_ms": 0}

def test_attach_kpts_matches_by_iou():
    from mono_state import attach_kpts
    kp = [[5.0, 5.0, 0.9]] * 17
    class D:  # duck-typed det
        def __init__(self, x, y, w, h, kpts): self.x, self.y, self.w, self.h, self.kpts = x, y, w, h, kpts
    tf = [(1, "person", 10.0, 10.0, 100.0, 200.0, 0.9, False)]
    out = attach_kpts(tf, [D(12.0, 11.0, 98.0, 205.0, kp), D(500.0, 500.0, 50.0, 50.0, None)])
    assert out[0][8] == kp
    out2 = attach_kpts(tf, [D(500.0, 500.0, 50.0, 50.0, kp)])   # no overlap -> no kpts
    assert len(out2[0]) == 8
```

In `mono_state.py`: bump `"v": 2`; in the track loop add `if len(t) > 8 and t[8] is not None: tracks[-1]["kpts"] = t[8]`; add `"gesture": extras.get("gesture") or {"name": None, "track_id": None, "held_ms": 0}` to the snapshot; implement `attach_kpts(tracks_full, dets)` — for each 8-tuple track, IoU-match against dets that have non-None `.kpts` (IoU > 0.5, best wins) and append the det's kpts as element 9, else leave the 8-tuple. Run `python3 -m pytest test_mono_state.py -v` → all pass (old v1 tests updated only where they assert `s["v"]`).

- [ ] **Step 2: Pose wrapper.** `hosts/pi5/hailo_yolo_pose.py` (class `HailoYoloPose`) mirroring `HailoYolo26`'s structure verbatim (`__init__` vdevice/infer-model/FLOAT32/letterbox identical, HEF-derived input size) with `infer()` decoding boxes + keypoints **per the Task 1 model card** (`models/yolov8m_pose_h10.md`) and un-mapping both through the same `(r, px, py)` letterbox transform. `Det` objects expose `.x .y .w .h .label .conf .kpts` (`label` fixed `"person"`). `__main__` probe (card's appendix pattern): run on `/tmp/cm3.jpg`, print boxes + first 3 kpts each. Verify standalone on the Pi: `ssh garuda@10.42.0.128 'python3 ~/garuda-sdk-deploy/hosts/pi5/hailo_yolo_pose.py ~/garuda-sdk-deploy/models/yolov8m_pose_h10.hef /tmp/cm3.jpg'`.

- [ ] **Step 3: Wire the mode into garuda_mono.py** (mirror the `_world` pattern exactly):
  - Module level: `POSE_HEF = os.environ.get("GARUDA_MONO_POSE", "/home/garuda/garuda-sdk-deploy/models/yolov8m_pose_h10.hef")`, `_pose = {"det": None}`, `_gest = {"fsm": None, "handoff_fsm": None, "out": {"name": None, "track_id": None, "held_ms": 0}}`.
  - `_set_detector`: allow `mode=pose` — lazy-construct `HailoYoloPose(POSE_HEF, vdevice=vd)` on first switch inside try/except (any failure → `bad(reason)`, primary stays); on success `_detector.update(mode="pose", prompt=[], gen=+1)` and `_gest["fsm"] = GestureFSM(); _gest["handoff_fsm"] = GestureFSM()`.
  - `loop()`: `active` selection becomes three-way (`_pose["det"]` when mode is pose); after `tracks_full` is built, when mode is pose: `tracks_full = attach_kpts(tracks_full, dets)`, then TWO classifications per spec §2/§4:
    - **Subject FSM** (`_gest["fsm"]`): the locked track if `lid` matches one, else the largest-area kpts-bearing track — `classify_pose` on its kpts → `fsm.update(...)`.
    - **Handoff FSM** (`_gest["handoff_fsm"]`, only evaluated while a lock exists): the largest-area kpts-bearing track that is NOT the locked one; feed `fsm.update(name if name == "follow_me" else None)` — a bystander can hand off with FOLLOW ME but can never STOP/POINT.
    - Result: if the handoff FSM is active, `_gest["out"] = {"name": "follow_me", "track_id": <bystander id>, "held_ms": ...}` (handoff wins); else the subject FSM's result with the subject's id. Store under `_lock` with the `_state.update`. Non-pose modes: `_gest["out"] = {"name": None, "track_id": None, "held_ms": 0}` and both FSMs reset.
  - `_snapshot()`: pass `"gesture": dict(_gest["out"])` in extras.
  - The pose failure guard mirrors `WORLD_MAX_FAILS` (revert to primary, same message pattern).
- [ ] **Step 4: Deploy + short-window verify** (mono via button/systemctl, then stop): switch `POST /api/detector?mode=pose`, `curl /api/state` shows `detector.mode == "pose"`, tracks carry 17-entry `kpts`, standing in view with both arms raised ≥1 s flips `gesture.name` to `"stop"` (verify via repeated `/api/state` curls). `mode=primary` restores. **Stop mono after.**
- [ ] **Step 5: Commit** — `git add hosts/pi5/ && git commit -m "feat(mono): pose detector mode — kpts through ByteTrack, gestures in snapshot v2 (AGPL eval-only)"`

---

### Task 4: portal — skeleton overlay, gesture semantics, HUD

**Files:**
- Modify: `services/garuda-portal/follow_pipeline.py` (paused), `services/garuda-portal/portal.py` (gesture reactor wiring), `static/app.js`, `static/index.html`, `static/style.css`
- Create: `services/garuda-portal/gesture_actions.py`
- Test: `services/garuda-portal/tests/test_follow_pipeline_paused.py`, `tests/test_gesture_actions.py`, extend `tests/test_static_routes.py` (ids)

**Interfaces:**
- Consumes: snapshot v2 `tracks[].kpts` (pixels) + `gesture` via the existing `mono` bus events; mono's `POST /api/mono_click?x=&y=` (pixels) for FOLLOW ME.
- Produces: `FollowPipeline.paused: bool` — while True, `on_detections` still returns tracks but its command has `valid=False, reason="paused (gesture)"` and zeroed rates. `GestureReactor(bus, pipeline, mono_base, post=urllib-POST).start()` — subscribes to the bus, acts once per gesture ONSET (name transition): `"stop"` → `pipeline.paused = True`; `"follow_me"` → `pipeline.paused = False`, `pipeline.set_pinned(None)`, POST `mono_click` at the gesturing track's bbox center (pixels from the snapshot's own track entry); `"point_*"` → no action. HUD ids: `#trk-gesture` (chip), skeleton drawn on `#follow-overlay`.

- [ ] **Step 1: TDD `paused`** — `tests/test_follow_pipeline_paused.py`: build the minimal detset dict the existing follow tests use (copy the fixture shape from `tests/test_follow_integration.py`), assert: unpaused → command unchanged vs baseline; `p.paused = True` → same tracks, `command["valid"] is False`, `command["reason"] == "paused (gesture)"`, yaw/forward/vertical all 0.0; `paused = False` → recovers; **`p.paused = True; p.set_pinned(4)` → `p.paused` is False** (spec §3: a video click — which pins — clears a gesture STOP). Implement in `follow_pipeline.py`: `self.paused = False` in `__init__`; `set_pinned` sets `self.paused = False`; at the top of the command-construction in `on_detections`, if `self.paused`, short-circuit the command fields accordingly (tracks list construction unchanged). Run the file + the whole portal suite.
- [ ] **Step 2: TDD `GestureReactor`** — `tests/test_gesture_actions.py`: fake bus (reuse `events.EventBus`), stub pipeline (records attribute sets/calls), `post` spy. Publish `mono` snapshots: (a) gesture None→"stop" onset → `pipeline.paused` True, exactly one action despite 5 repeat events with "stop"; (b) →"follow_me" with `track_id` 7 whose track bbox is (100,50,80,160) → `post` called once with `.../api/mono_click?x=140&y=130` (center), paused False; (c) "point_right" → no calls; (d) malformed events (no gesture key / unknown name) → ignored, no crash. Implement `gesture_actions.py` (~60 lines: daemon thread on `bus.subscribe()`, tracks last name, urllib POST with 2 s timeout, per-event try/except drop). Wire in `portal.py` `main()` next to `MonoClient`: `GestureReactor(bus, follow_pipeline, os.environ.get("GARUDA_MONO_URL", "http://127.0.0.1:8098")).start()`.
- [ ] **Step 3: UI.** `index.html`: add `<span id="trk-gesture" class="gesture-chip hidden"></span>` next to `#tracking-banner` in the sidebar. `style.css`: `.gesture-chip { padding: .25rem .6rem; border-radius: 12px; font-size: .8rem; font-weight: 600; background: #16241a; color: #5ed582; border: 1px solid #2d5a2d; } .gesture-chip.hidden { display: none; }`. `app.js`: in `onMono(s)` store `window._monoTracks = s.tracks || []` and update the chip (`s.gesture.name` → text `✋ STOP` / `🙋 FOLLOW ME` / `👉 POINT L/R` + unhide, null → hide); in `drawFollowOverlay()` after the box pass, for each `_monoTracks` entry with `kpts`, draw COCO bones (`const BONES = [[5,7],[7,9],[6,8],[8,10],[5,6],[11,12],[5,11],[6,12],[11,13],[13,15],[12,14],[14,16],[0,5],[0,6]]`) as lines between keypoints with conf ≥ 0.35, coords divided by `_trkFrame.width/height` then scaled to canvas (same transform the boxes use), plus 3 px joint dots. Add `trk-gesture` to the structural id test.
- [ ] **Step 4: Full portal suite** (`python3 -m pytest tests/ -v`) → green; deploy portal files + short-window bench check: pose mode on, skeleton visible over the person, STOP chip appears after ~1 s hold and drone-dir zeroes, FOLLOW ME re-locks, POINT chip shows direction. **Stop mono after.**
- [ ] **Step 5: Commit** — `git add services/garuda-portal/ && git commit -m "feat(portal): skeleton overlay + gesture semantics (STOP/FOLLOW ME/POINT, server-side reactor)"`

---

### Task 5: bench gate, threshold freeze, docs close-out (PSU-gated)

**Files:**
- Modify: `bench/benchlog.md`, `CLAUDE.md` §7, the spec (status line), `hosts/pi5/mono_gestures.py` + `models/yolo26m_pose_h10.md` (only if constants change)

- [ ] **Step 1: WAIT for the 27 W PSU** (owner confirms clean rail: `get_throttled` 0x0 under load). Everything above this line ships without it; this task's numbers do not.
- [ ] **Step 2: fps gate.** Pose mode + depth co-resident, stream viewer attached, 60 s: processed-frame rate from `/api/events` cadence, `vcgencmd get_throttled`, temp → benchlog. GATE: sustains camera fps (15) with low throttle bits 0x0. FAIL → fetch the zoo `yolov8s_pose` HEF (same zoo URL pattern as the m variant — own-export path is exporter-gated), re-gate, record both results either way.
- [ ] **Step 3: Threshold tuning.** Owner runs the 3 gestures at 2 m and 4 m, notes false/missed triggers; adjust the four constants in `mono_gestures.py` minimally, re-run unit tests, freeze the final values in the module + model card. Record the qualitative outcome (which gestures at which range) in benchlog — no invented precision.
- [ ] **Step 4: Close-out.** Red-team note in benchlog (rule 9: could the gate numbers flatter — single-person scene? cool SoC?). Spec status → `implemented <date>`. CLAUDE.md §7: pose/gesture state + next steps. Commit: `docs: pose-gestures close-out — bench gate, frozen thresholds, resume state`. Merge decision → owner (superpowers:finishing-a-development-branch).
