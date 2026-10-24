# garuda-follow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mono subject-following per `docs/superpowers/specs/2026-07-30-garuda-follow-design.md` — a tracker, target selector, and safety-gated visual-servoing controller, proven by (a) a pure closed-loop convergence test, (b) portal integration on the real CM3 detection stream, and (c) a live PX4-SITL closed loop with a synthetic target.

**Architecture:** All follow logic is pure Python (stdlib only) in `services/garuda-follow/`, consumed by both the portal (real perception path) and the SITL harness (simulated closed loop). Nothing arms; all flight is `GARUDA_SIM`-gated simulation.

**Tech Stack:** Python 3.11+ stdlib (dataclasses, math); pytest. Reuses the Phase-5 PX4 SITL + ROS 2 Jazzy bridge for the live demo. No new third-party deps.

## Global Constraints

- Pure modules (`tracker`, `target_selector`, `follow_controller`, `sim_kinematics`, `projection`) are **stdlib-only** — no numpy, no camera/Gst/ROS imports — so `pytest services/ -v` passes on any Linux box with no hardware.
- `sitl_followme.py` MUST refuse to run unless `GARUDA_SIM=1` (CLAUDE.md safety rule; mirror `bench/failsafe_demo.py`'s gate exactly).
- The follow controller MUST return a *hold* (zero velocities, `valid=False`, `reason` set) on a lost / low-confidence / stale / implausible target. This safety gate is load-bearing.
- Licence: BSD-3, no GPLv3.
- Detections consumed as dicts shaped like `garuda_interfaces.Detection.to_dict()`: `{"class_id","label","confidence","x","y","w","h"}` with bbox normalized to [0,1].
- Bboxes everywhere are normalized `(x, y, w, h)` with `(x,y)` the top-left corner, matching the existing `Detection` schema.
- Commit after every task with test evidence; messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Full suite `pytest services/ -v` must stay green (portal suite included) — report complete output where a task changes shared code.

## File Structure

```
interfaces/garuda_interfaces/messages.py         # add Track, FollowCommand (+ __init__ exports)
services/garuda-follow/tracker.py                # IoU/ByteTrack-lite tracker (pure)
services/garuda-follow/target_selector.py        # pick the track to follow (pure)
services/garuda-follow/follow_controller.py      # visual-servoing controller + safety gate (pure)
services/garuda-follow/sim_kinematics.py         # 2D drone+target sim for the convergence test (pure)
services/garuda-follow/projection.py             # world->image pinhole projection (pure)
services/garuda-follow/sitl_followme.py          # live PX4-SITL closed loop (GARUDA_SIM gated)
services/garuda-follow/tests/test_tracker.py
services/garuda-follow/tests/test_target_selector.py
services/garuda-follow/tests/test_follow_controller.py
services/garuda-follow/tests/test_convergence.py
services/garuda-follow/tests/test_projection.py
services/garuda-portal/portal.py                 # modify: run tracker/selector/controller on detections
services/garuda-portal/static/{index.html,app.js} # modify: track IDs + Follow control
bench/benchlog.md                                # append: live SITL follow-me convergence result
```

---

### Task 1: Track & FollowCommand message schemas

**Files:**
- Modify: `interfaces/garuda_interfaces/messages.py`, `interfaces/garuda_interfaces/__init__.py`
- Test: `services/garuda-follow/tests/test_schemas.py`

**Interfaces:**
- Produces: `Track(id:int, class_id:int, label:str, bbox:tuple, confidence:float, hits:int, age:int, time_since_update:int)` and `FollowCommand(t_ns:int, target_id, yaw_rate:float, forward_vel:float, vertical_vel:float, valid:bool, reason:str)` dataclasses, each with `to_dict()`. Every later task uses these.

- [ ] **Step 1: Write the failing test**

```python
# services/garuda-follow/tests/test_schemas.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
from garuda_interfaces import Track, FollowCommand

def test_track_to_dict_roundtrips_fields():
    t = Track(id=3, class_id=0, label="person", bbox=(0.1, 0.2, 0.3, 0.4),
              confidence=0.82, hits=5, age=5, time_since_update=0)
    d = t.to_dict()
    assert d["id"] == 3 and d["label"] == "person"
    assert d["bbox"] == [0.1, 0.2, 0.3, 0.4]        # tuple serialized as list
    assert d["confidence"] == 0.82 and d["time_since_update"] == 0

def test_follow_command_hold_shape():
    c = FollowCommand(t_ns=123, target_id=None, yaw_rate=0.0, forward_vel=0.0,
                      vertical_vel=0.0, valid=False, reason="no target")
    d = c.to_dict()
    assert d["valid"] is False and d["reason"] == "no target"
    assert d["yaw_rate"] == 0.0 and d["target_id"] is None
```

- [ ] **Step 2: Run, verify fail** — `pytest services/garuda-follow/tests/test_schemas.py -v` → ImportError.
- [ ] **Step 3: Implement** — append to `messages.py`:

```python
@dataclass
class Track:
    id: int
    class_id: int
    label: str
    bbox: tuple                  # normalized (x, y, w, h), top-left origin
    confidence: float
    hits: int = 1
    age: int = 1
    time_since_update: int = 0

    def to_dict(self) -> dict:
        return {"id": self.id, "class_id": self.class_id, "label": self.label,
                "bbox": list(self.bbox), "confidence": self.confidence,
                "hits": self.hits, "age": self.age,
                "time_since_update": self.time_since_update}


@dataclass
class FollowCommand:
    t_ns: int
    target_id: object            # int or None
    yaw_rate: float              # rad/s, +right
    forward_vel: float           # m/s, +toward target
    vertical_vel: float          # m/s, +up
    valid: bool
    reason: str
    schema: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {"t_ns": self.t_ns, "target_id": self.target_id,
                "yaw_rate": self.yaw_rate, "forward_vel": self.forward_vel,
                "vertical_vel": self.vertical_vel, "valid": self.valid,
                "reason": self.reason, "schema": self.schema}
```

Add `Track` and `FollowCommand` to `__init__.py`'s imports and `__all__`.

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(interfaces): Track and FollowCommand schemas`.

---

### Task 2: IoU / ByteTrack-lite tracker

**Files:**
- Create: `services/garuda-follow/tracker.py`
- Test: `services/garuda-follow/tests/test_tracker.py`

**Interfaces:**
- Consumes: `Track` (Task 1).
- Produces: `iou(a, b) -> float` (normalized xywh boxes); `Tracker(iou_threshold=0.3, max_age=15)` with `update(detections: list[dict], frame_wh=(1,1)) -> list[Track]`. Detections are dicts with `class_id,label,confidence,x,y,w,h`. IDs are monotonic ints. Later tasks call `Tracker().update(...)`.

- [ ] **Step 1: Write failing tests**

```python
# services/garuda-follow/tests/test_tracker.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tracker import Tracker, iou

def det(x, y, w, h, label="person", conf=0.9, cid=0):
    return {"class_id": cid, "label": label, "confidence": conf, "x": x, "y": y, "w": w, "h": h}

def test_iou_identical_is_one_and_disjoint_is_zero():
    assert iou((0.1, 0.1, 0.2, 0.2), (0.1, 0.1, 0.2, 0.2)) == 1.0
    assert iou((0.0, 0.0, 0.1, 0.1), (0.5, 0.5, 0.1, 0.1)) == 0.0

def test_same_object_keeps_id_across_frames():
    tr = Tracker()
    a = tr.update([det(0.40, 0.40, 0.20, 0.20)])
    b = tr.update([det(0.42, 0.41, 0.20, 0.20)])  # small motion, high IoU
    assert len(a) == 1 and len(b) == 1
    assert a[0].id == b[0].id
    assert b[0].hits == 2 and b[0].time_since_update == 0

def test_distinct_objects_get_distinct_ids():
    tr = Tracker()
    out = tr.update([det(0.1, 0.1, 0.1, 0.1), det(0.8, 0.8, 0.1, 0.1)])
    assert len({t.id for t in out}) == 2

def test_track_dropped_after_max_age():
    tr = Tracker(max_age=2)
    first = tr.update([det(0.4, 0.4, 0.2, 0.2)])[0]
    for _ in range(3):
        out = tr.update([])            # target gone
    assert all(t.id != first.id for t in out) and out == []

def test_reappearing_after_drop_gets_new_id():
    tr = Tracker(max_age=1)
    a = tr.update([det(0.4, 0.4, 0.2, 0.2)])[0]
    tr.update([]); tr.update([])       # dropped
    c = tr.update([det(0.4, 0.4, 0.2, 0.2)])[0]
    assert c.id != a.id                # honest: no re-ID this increment
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `tracker.py`:

```python
"""Dependency-free IoU tracker (ByteTrack-lite: greedy IoU association).
No re-identification — a track lost past max_age is dropped, a returning object
gets a new id (honest mono limitation, see the spec's non-goals)."""
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "interfaces"))
from garuda_interfaces import Track


def iou(a, b) -> float:
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0.0, x2 - x1), max(0.0, y2 - y1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


class Tracker:
    def __init__(self, iou_threshold=0.3, max_age=15):
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self._tracks: list[Track] = []
        self._next_id = 1

    def update(self, detections, frame_wh=(1, 1)) -> list[Track]:
        dets = [(d, (d["x"], d["y"], d["w"], d["h"])) for d in detections]
        used = set()
        # greedy match existing tracks to detections by best IoU
        for t in self._tracks:
            best_j, best_iou = -1, self.iou_threshold
            for j, (d, box) in enumerate(dets):
                if j in used or d["class_id"] != t.class_id:
                    continue
                v = iou(t.bbox, box)
                if v >= best_iou:
                    best_iou, best_j = v, j
            if best_j >= 0:
                d, box = dets[best_j]
                used.add(best_j)
                t.bbox = box
                t.confidence = d["confidence"]
                t.label = d["label"]
                t.hits += 1
                t.age += 1
                t.time_since_update = 0
            else:
                t.age += 1
                t.time_since_update += 1
        # spawn new tracks for unmatched detections
        for j, (d, box) in enumerate(dets):
            if j in used:
                continue
            self._tracks.append(Track(id=self._next_id, class_id=d["class_id"],
                                      label=d["label"], bbox=box,
                                      confidence=d["confidence"]))
            self._next_id += 1
        # drop stale
        self._tracks = [t for t in self._tracks if t.time_since_update <= self.max_age]
        return [t for t in self._tracks if t.time_since_update == 0]
```

- [ ] **Step 4: Run, verify pass** (all 5).
- [ ] **Step 5: Commit** — `feat(garuda-follow): IoU/ByteTrack-lite tracker`.

---

### Task 3: Target selector

**Files:**
- Create: `services/garuda-follow/target_selector.py`
- Test: `services/garuda-follow/tests/test_target_selector.py`

**Interfaces:**
- Consumes: `Track`.
- Produces: `select(tracks, follow_class="person", pinned_id=None) -> Track | None`.

- [ ] **Step 1: Failing tests**

```python
# services/garuda-follow/tests/test_target_selector.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from garuda_interfaces import Track
from target_selector import select

def trk(id, label, conf):
    return Track(id=id, class_id=0, label=label, bbox=(0.4, 0.4, 0.2, 0.2), confidence=conf)

def test_auto_picks_highest_confidence_of_class():
    tracks = [trk(1, "person", 0.5), trk(2, "person", 0.9), trk(3, "car", 0.99)]
    assert select(tracks, follow_class="person").id == 2

def test_pinned_id_wins_when_present():
    tracks = [trk(1, "person", 0.5), trk(2, "person", 0.9)]
    assert select(tracks, follow_class="person", pinned_id=1).id == 1

def test_pinned_absent_falls_back_to_auto():
    tracks = [trk(2, "person", 0.9)]
    assert select(tracks, follow_class="person", pinned_id=1).id == 2

def test_none_when_no_class_match():
    assert select([trk(3, "car", 0.99)], follow_class="person") is None
    assert select([], follow_class="person") is None
```

- [ ] **Step 2: Fail → Step 3: Implement**

```python
"""Pick which track to follow: a pinned id if still present, else the
highest-confidence track of the follow class."""
from __future__ import annotations

def select(tracks, follow_class="person", pinned_id=None):
    if pinned_id is not None:
        for t in tracks:
            if t.id == pinned_id:
                return t
    candidates = [t for t in tracks if t.label == follow_class]
    if not candidates:
        return None
    return max(candidates, key=lambda t: t.confidence)
```

- [ ] **Step 4: Pass → Step 5: Commit** — `feat(garuda-follow): target selector (auto + pinned)`.

---

### Task 4: Follow controller + safety gate

**Files:**
- Create: `services/garuda-follow/follow_controller.py`
- Test: `services/garuda-follow/tests/test_follow_controller.py`

**Interfaces:**
- Consumes: `Track`, `FollowCommand`.
- Produces: `FollowConfig` dataclass (gains/thresholds) and `compute(target, frame_wh, cfg, t_ns=0) -> FollowCommand`. `target` is a `Track` or `None`. Task 5/6 call this.

- [ ] **Step 1: Failing tests**

```python
# services/garuda-follow/tests/test_follow_controller.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from garuda_interfaces import Track
from follow_controller import compute, FollowConfig

CFG = FollowConfig()

def target(cx, h, conf=0.9, tsu=0):
    # bbox centered horizontally at cx, vertically at 0.5, height h
    return Track(id=1, class_id=0, label="person",
                 bbox=(cx - 0.1, 0.5 - h / 2, 0.2, h), confidence=conf, time_since_update=tsu)

def test_centered_at_desired_size_is_near_zero_and_valid():
    c = compute(target(0.5, CFG.desired_height_frac), (1, 1), CFG)
    assert c.valid
    assert abs(c.yaw_rate) < 1e-6 and abs(c.forward_vel) < 1e-6

def test_target_left_yaws_left():
    c = compute(target(0.2, CFG.desired_height_frac), (1, 1), CFG)
    assert c.valid and c.yaw_rate < 0        # negative = turn left toward target

def test_target_right_yaws_right():
    c = compute(target(0.8, CFG.desired_height_frac), (1, 1), CFG)
    assert c.valid and c.yaw_rate > 0

def test_small_bbox_moves_forward_large_moves_back():
    small = compute(target(0.5, CFG.desired_height_frac * 0.5), (1, 1), CFG)
    large = compute(target(0.5, CFG.desired_height_frac * 1.5), (1, 1), CFG)
    assert small.forward_vel > 0 and large.forward_vel < 0

def test_output_is_clamped():
    c = compute(target(0.0, CFG.min_height_frac + 0.001), (1, 1), CFG)  # far off-center, far
    assert abs(c.yaw_rate) <= CFG.max_yaw_rate + 1e-9
    assert abs(c.forward_vel) <= CFG.max_forward_vel + 1e-9

def test_none_target_holds():
    c = compute(None, (1, 1), CFG)
    assert not c.valid and c.reason and c.forward_vel == 0.0 and c.yaw_rate == 0.0

def test_low_confidence_holds():
    c = compute(target(0.5, CFG.desired_height_frac, conf=CFG.min_confidence - 0.01), (1, 1), CFG)
    assert not c.valid and "confidence" in c.reason

def test_stale_target_holds():
    c = compute(target(0.5, CFG.desired_height_frac, tsu=CFG.max_stale_frames + 1), (1, 1), CFG)
    assert not c.valid and "stale" in c.reason

def test_implausible_size_holds():
    big = compute(target(0.5, CFG.max_height_frac + 0.05), (1, 1), CFG)
    tiny = compute(target(0.5, CFG.min_height_frac - 0.001), (1, 1), CFG)
    assert not big.valid and not tiny.valid
```

- [ ] **Step 2: Fail → Step 3: Implement**

```python
"""Image-based visual servoing for mono subject-following, with a load-bearing
safety gate: a lost / low-confidence / stale / implausible target yields a HOLD
(zero velocities, valid=False) — never a chase. This is the odometry
covariance/validity discipline applied to following."""
from __future__ import annotations
import sys, pathlib
from dataclasses import dataclass
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "interfaces"))
from garuda_interfaces import FollowCommand


@dataclass
class FollowConfig:
    desired_height_frac: float = 0.5   # target bbox should fill ~half the frame height
    yaw_gain: float = 2.0              # rad/s per unit horizontal error
    forward_gain: float = 3.0         # m/s per unit (desired-actual) height error
    vertical_gain: float = 1.0        # m/s per unit vertical error
    deadzone: float = 0.03            # ignore errors this small (fraction of frame)
    max_yaw_rate: float = 0.8
    max_forward_vel: float = 2.0
    max_vertical_vel: float = 1.0
    min_confidence: float = 0.35
    max_stale_frames: int = 5
    min_height_frac: float = 0.05
    max_height_frac: float = 0.95


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _dz(err, dz):
    return 0.0 if abs(err) < dz else err


def compute(target, frame_wh, cfg, t_ns=0) -> FollowCommand:
    def hold(reason):
        return FollowCommand(t_ns=t_ns, target_id=(target.id if target else None),
                             yaw_rate=0.0, forward_vel=0.0, vertical_vel=0.0,
                             valid=False, reason=reason)
    if target is None:
        return hold("no target")
    if target.confidence < cfg.min_confidence:
        return hold(f"low confidence {target.confidence:.2f}")
    if target.time_since_update > cfg.max_stale_frames:
        return hold(f"stale target ({target.time_since_update} frames)")
    x, y, w, h = target.bbox
    if h < cfg.min_height_frac or h > cfg.max_height_frac:
        return hold(f"implausible size h={h:.2f}")

    cx, cy = x + w / 2.0, y + h / 2.0
    ex = _dz(cx - 0.5, cfg.deadzone)          # +: target right of center
    ey = _dz(cy - 0.5, cfg.deadzone)          # +: target below center
    eh = cfg.desired_height_frac - h          # +: target too small (far) -> go forward

    yaw = _clamp(cfg.yaw_gain * ex, -cfg.max_yaw_rate, cfg.max_yaw_rate)
    fwd = _clamp(cfg.forward_gain * eh, -cfg.max_forward_vel, cfg.max_forward_vel)
    vert = _clamp(-cfg.vertical_gain * ey, -cfg.max_vertical_vel, cfg.max_vertical_vel)
    return FollowCommand(t_ns=t_ns, target_id=target.id, yaw_rate=yaw,
                         forward_vel=fwd, vertical_vel=vert, valid=True, reason="tracking")
```

- [ ] **Step 4: Run, verify all pass.**
- [ ] **Step 5: Commit** — `feat(garuda-follow): visual-servoing controller with safety gate`.

---

### Task 5: Pure closed-loop convergence test

**Files:**
- Create: `services/garuda-follow/sim_kinematics.py`
- Test: `services/garuda-follow/tests/test_convergence.py`

**Interfaces:**
- Consumes: `compute`, `FollowConfig`, `Track`.
- Produces: `DroneState(x,y,z,yaw)`, `step(state, cmd, dt) -> DroneState`, and `target_bbox(state, target_xy, cfg, hfov=1.2, target_size=1.0) -> Track` — projects a world target into the drone's image plane as a bbox (bearing → horizontal position; range → bbox height). This is the simulated "camera" the controller closes the loop against.

- [ ] **Step 1: Failing test** (the convergence assertion IS the test)

```python
# services/garuda-follow/tests/test_convergence.py
import sys, pathlib, math
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from follow_controller import compute, FollowConfig
from sim_kinematics import DroneState, step, target_bbox

def test_follow_loop_converges_and_holds():
    cfg = FollowConfig()
    # drone starts at origin facing +x; target off to the side and far away
    st = DroneState(x=0.0, y=0.0, z=0.0, yaw=0.0)
    target_xy = (6.0, 4.0)
    errs = []
    for i in range(400):                       # 400 * 0.05s = 20s
        tgt = target_bbox(st, target_xy, cfg)
        cmd = compute(tgt, (1, 1), cfg)
        st = step(st, cmd, dt=0.05)
        bx, by, bw, bh = tgt.bbox
        cx = bx + bw / 2
        errs.append(abs(cx - 0.5) + abs(cfg.desired_height_frac - bh))
    # framing error must shrink and stay small (converged + held)
    assert errs[-1] < 0.1
    assert sum(errs[-20:]) / 20 < 0.1
    assert errs[-1] < errs[0]                   # actually improved

def test_target_behind_is_not_visible_then_recovers():
    # a target initially behind the drone -> bbox marked stale/None-like; the
    # controller holds, and once yaw brings it into view the loop proceeds.
    cfg = FollowConfig()
    st = DroneState(x=0.0, y=0.0, z=0.0, yaw=math.pi)  # facing -x, target at +x
    tgt = target_bbox(st, (6.0, 0.0), cfg)
    assert tgt.time_since_update > cfg.max_stale_frames  # behind camera -> not visible
```

- [ ] **Step 2: Fail → Step 3: Implement** `sim_kinematics.py`:

```python
"""A minimal 2.5D drone + world-target simulator used to prove the follow
controller converges in a true closed loop — no PX4 needed, deterministic, fast.
The drone yaws and translates per the controller's command; the target is
projected into its image plane (bearing -> horizontal bbox position, range ->
bbox height). The SITL harness (sitl_followme.py) does the same against real PX4."""
from __future__ import annotations
import math, sys, pathlib
from dataclasses import dataclass
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "interfaces"))
from garuda_interfaces import Track


@dataclass
class DroneState:
    x: float
    y: float
    z: float
    yaw: float                      # rad, 0 = +x


def step(state, cmd, dt):
    yaw = state.yaw + cmd.yaw_rate * dt
    x = state.x + cmd.forward_vel * math.cos(yaw) * dt
    y = state.y + cmd.forward_vel * math.sin(yaw) * dt
    z = state.z + cmd.vertical_vel * dt
    return DroneState(x=x, y=y, z=z, yaw=yaw)


def target_bbox(state, target_xy, cfg, hfov=1.2, target_size=6.0):
    """Project a world target into a normalized-image bbox. If the target is
    outside the horizontal FOV (incl. behind), return a Track flagged stale so
    the controller holds."""
    tx, ty = target_xy
    dx, dy = tx - state.x, ty - state.y
    rng = math.hypot(dx, dy)
    bearing = math.atan2(dy, dx) - state.yaw
    # normalize bearing to [-pi, pi]
    bearing = (bearing + math.pi) % (2 * math.pi) - math.pi
    cx = 0.5 + bearing / hfov       # 0..1 across the horizontal FOV
    h = min(0.95, target_size / max(rng, 0.1) * 0.1)   # farther -> smaller bbox
    visible = abs(bearing) < hfov / 2 and 0 <= cx <= 1
    tsu = 0 if visible else cfg.max_stale_frames + 1
    return Track(id=1, class_id=0, label="person",
                 bbox=(cx - 0.1, 0.5 - h / 2, 0.2, h), confidence=0.9,
                 hits=1, age=1, time_since_update=tsu)
```

  (Tune `target_size`/gains so the first test converges; the exact constant is
  the implementer's to settle against the assertion. The `desired_height_frac`
  should be reachable within the clamp — verify the equilibrium range is positive.)

- [ ] **Step 4: Run, verify convergence test passes.** If it doesn't converge, adjust gains in `FollowConfig` defaults (Task 4) and/or `target_size` here until it does; keep all Task 4 unit tests green.
- [ ] **Step 5: Commit** — `test(garuda-follow): pure closed-loop convergence proof`.

---

### Task 6: World→image projection (pure) for the SITL harness

**Files:**
- Create: `services/garuda-follow/projection.py`
- Test: `services/garuda-follow/tests/test_projection.py`

**Interfaces:**
- Produces: `project(target_world, drone_pos, drone_yaw, hfov=1.2, vfov=0.9, target_size=6.0) -> (bbox|None)` — a forward-facing camera model returning a normalized bbox, or `None` when the target is behind/outside the frustum. Used by `sitl_followme.py`. (Kept separate from `sim_kinematics.target_bbox` because the SITL path uses the real drone yaw from PX4 and may extend to full quaternion later.)

- [ ] **Step 1: Failing tests**

```python
# services/garuda-follow/tests/test_projection.py
import sys, pathlib, math
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from projection import project

def test_target_straight_ahead_centers():
    bbox = project((5.0, 0.0), (0.0, 0.0), 0.0)
    assert bbox is not None
    cx = bbox[0] + bbox[2] / 2
    assert abs(cx - 0.5) < 1e-6

def test_target_behind_is_none():
    assert project((-5.0, 0.0), (0.0, 0.0), 0.0) is None

def test_closer_target_has_taller_bbox():
    near = project((3.0, 0.0), (0.0, 0.0), 0.0)
    far = project((9.0, 0.0), (0.0, 0.0), 0.0)
    assert near[3] > far[3]
```

- [ ] **Step 2: Fail → Step 3: Implement** `projection.py` (a clean forward-facing pinhole; return None when `|bearing| >= hfov/2`). Mirror `sim_kinematics.target_bbox`'s bearing/range math but return `None` (not a stale Track) when out of frame.
- [ ] **Step 4: Pass → Step 5: Commit** — `feat(garuda-follow): world->image projection for SITL harness`.

---

### Task 7: Portal integration — track IDs + Follow control

**Files:**
- Modify: `services/garuda-portal/portal.py`, `services/garuda-portal/static/index.html`, `services/garuda-portal/static/app.js`
- Test: `services/garuda-portal/tests/test_follow_integration.py`

**Interfaces:**
- Consumes: `Tracker`, `select`, `compute`, `FollowConfig` (import from `services/garuda-follow/` — add that dir to the portal's `sys.path`, or copy the pure modules into the deploy; the plan uses a `FollowPipeline` wrapper so the portal has one entry point).
- Produces: a `FollowPipeline` (new small module `services/garuda-portal/follow_pipeline.py`) with `on_detections(detset_dict) -> {"tracks": [...], "command": {...}}` and `set_pinned(track_id)`. The portal calls it on each `detections` event and publishes a `follow` event; the Inference panel renders track IDs + the command.

- [ ] **Step 1: Failing test** (pipeline is the testable unit; UI is manual)

```python
# services/garuda-portal/tests/test_follow_integration.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from follow_pipeline import FollowPipeline

def detset(dets):
    return {"detections": dets, "frame": {"width": 640, "height": 640}}

def person(cx, h, conf=0.9):
    return {"class_id": 0, "label": "person", "confidence": conf,
            "x": cx - 0.1, "y": 0.5 - h / 2, "w": 0.2, "h": h}

def test_pipeline_tracks_and_commands():
    fp = FollowPipeline()
    fp.on_detections(detset([person(0.5, 0.5)]))
    out = fp.on_detections(detset([person(0.5, 0.5)]))   # 2nd frame -> stable track
    assert len(out["tracks"]) == 1
    assert out["tracks"][0]["id"] == 1
    assert out["command"]["valid"] is True

def test_pipeline_holds_when_no_person():
    fp = FollowPipeline()
    out = fp.on_detections(detset([{"class_id": 2, "label": "car", "confidence": 0.9,
                                    "x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}]))
    assert out["command"]["valid"] is False

def test_pinned_id_is_followed():
    fp = FollowPipeline()
    fp.on_detections(detset([person(0.3, 0.5), person(0.7, 0.5)]))
    out = fp.on_detections(detset([person(0.3, 0.5), person(0.7, 0.5)]))
    fp.set_pinned(out["tracks"][1]["id"])
    out2 = fp.on_detections(detset([person(0.3, 0.5), person(0.7, 0.5)]))
    assert out2["command"]["target_id"] == out["tracks"][1]["id"]
```

- [ ] **Step 2: Fail → Step 3: Implement** `follow_pipeline.py` (wraps `Tracker` + `select` + `compute`; holds `pinned_id`); wire it into `portal.py`'s detection handling to publish a `follow` event; extend `app.js` to draw track IDs on the annotated feed's detection list and add a Follow selector + command readout in the Inference panel; extend `index.html` accordingly. Keep the portal's existing tests green.
- [ ] **Step 4: Run `pytest services/ -v` (full suite green). Smoke the portal locally** (fake detections → `/api/events` emits `follow`).
- [ ] **Step 5: Commit** — `feat(portal): track IDs + follow control on the inference feed`.

---

### Task 8: Live PX4-SITL closed loop + benchlog

**Files:**
- Create: `services/garuda-follow/sitl_followme.py`
- Modify: `bench/benchlog.md`

**Interfaces:**
- Consumes: `compute`, `FollowConfig`, `project`, the Phase-5 PX4 SITL + ROS 2 Jazzy bridge.

- [ ] **Step 1: Implement `sitl_followme.py`** — `GARUDA_SIM=1` gate at the top (exit otherwise, mirroring `bench/failsafe_demo.py`). Subscribe to the drone pose from PX4 (`/fmu/out/vehicle_local_position` via the bridge), maintain a synthetic world target, each tick: `project(target, drone_xy, drone_yaw)` → bbox (or None) → `compute(...)` → publish the resulting velocity/yaw-rate as an offboard `TrajectorySetpoint`; arm + offboard as in `bench/offboard_demo.py`. Log framing error each second.
- [ ] **Step 2: Run on the bench** — start PX4 SITL + the XRCE agent container (as in the Phase-5 benchlog), then `GARUDA_SIM=1 … sitl_followme.py`. Capture the framing-error-over-time and the final converged/held state.
- [ ] **Step 3: Verify GARUDA_SIM gate** — running without the env var prints the refusal and exits non-zero; capture that too.
- [ ] **Step 4: Append to `bench/benchlog.md`** — a dated "garuda-follow: mono follow-me SITL closed loop" entry with the convergence evidence (framing error start→end, hold), honest scope notes (synthetic target; real perception path proven separately in the portal; mono size-proxy range), and the `GARUDA_SIM` gate confirmation. Numbers only from the actual run (CLAUDE.md rule 1).
- [ ] **Step 5: Commit** — `feat(garuda-follow): live PX4-SITL mono follow-me loop; benchlog`.

---

### Task 9: README/docs update

**Files:**
- Modify: `README.md`, `services/garuda-follow/README.md` (new)

- [ ] **Step 1:** Add a "Subject-following (garuda-follow)" subsection to the README results/what-we-implemented, with the SITL convergence result and the honest mono-vs-depth framing. Write a short `services/garuda-follow/README.md` describing the module and how to run the SITL demo.
- [ ] **Step 2:** `pytest services/ -v` green; commit — `docs: garuda-follow module + results`. Push both repos if desired.
