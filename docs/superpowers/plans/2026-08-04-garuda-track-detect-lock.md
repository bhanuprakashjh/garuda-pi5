# garuda-track (S1: detect + track + lock) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A live perception service that detects COCO objects on the CM3 color camera, tracks them, lets the operator click one in the portal to lock it, and publishes a `TrackedObjects` contract for downstream services.

**Architecture:** One native Python service `services/garuda-track` (capture → Hailo YOLOv8m → ByteTrack → lock FSM → mmap IPC), a `TrackedObjects` contract in `interfaces/`, and a `garuda-portal` overlay with click-to-lock. Pure-logic modules (contract, IPC, tracker, FSM, command channel) are built and unit-tested first; hardware wiring and portal come last.

**Tech Stack:** Python 3.13, NumPy, OpenCV (Apache-2.0), Hailo RT (existing `garuda-inference` decode path), the `garuda-camera` backend, mmap/seqlock IPC (generalized from `services/garuda-mask/mask_ipc.py`), a vendored lean ByteTrack (MIT algorithm), UDS datagram command channel.

## Global Constraints

- **Numbers are sacred** (CLAUDE.md §6.1): every fps/CPU/thermal figure comes from a harness run on this bench, recorded in `bench/benchlog.md` with date + `versions.md` snapshot; unmeasured = `TBD-bench`. Never invent a number in code/docs/tests.
- **No GPL** in this workstream: ByteTrack (MIT, vendored), OpenCV (Apache-2.0), Hailo tooling only. State licences in the landing commit.
- **No new deps beyond what's already on the bench** unless flagged: **scipy is NOT installed** — do not use it (tracker association is greedy/NumPy-only). If a pip dep lands, add a skeleton Yocto recipe (CLAUDE.md §6.6).
- **Single-tenant NPU + camera**: only one of {garuda-depth, garuda-track} runs at a time; the run script stops `garuda-depth` first (idempotent, CLAUDE.md §6.3).
- **No Pi-specific calls outside `hosts/pi5/`**; detection sits behind a plugin boundary (CLAUDE.md §1).
- **Bbox convention is `[x, y, w, h]` in detection-frame pixels** everywhere in S1 (top-left origin). `center = [x + w/2, y + h/2]`.
- **Hardware tests** are marked `@requires_hw(...)` and skip cleanly in CI (CLAUDE.md §6.7).

## File Structure

- `interfaces/garuda_interfaces/tracked_objects.py` — `TrackedObject`, `TrackedObjects`, `LockCommand`, enums, (de)serialization. THE contract.
- `services/garuda-track/track_ipc.py` — `TrackIPC` (generalized seqlock mmap, JSON payload).
- `services/garuda-track/bytetrack.py` — vendored lean ByteTrack (linear-motion + two-stage greedy IoU).
- `services/garuda-track/lock_fsm.py` — `LockFSM` + `select_by_point`.
- `services/garuda-track/cmd_channel.py` — UDS datagram lock-command mailbox (non-blocking).
- `services/garuda-track/detector.py` — `Detector` protocol + `HailoYoloDetector` (Hailo path) + `FakeDetector` (tests).
- `services/garuda-track/track_service.py` — main loop wiring it all.
- `services/garuda-track/tests/` — unit tests.
- `services/garuda-portal/track_overlay.py` — MJPEG overlay (boxes from IPC) + click→lock; wired into `portal.py`.
- `hosts/pi5/run_track.sh` — idempotent run (stop depth, launch service+portal).
- `hosts/pi5/track_bench.py` — fps/CPU/throttle harness → benchlog.

---

### Task 1: TrackedObjects contract

**Files:**
- Create: `interfaces/garuda_interfaces/tracked_objects.py`
- Test: `interfaces/garuda_interfaces/tests/test_tracked_objects.py`

**Interfaces:**
- Produces: `TrackedObject`, `TrackedObjects`, `LockCommand` dataclasses; `TrackedObjects.to_json()/from_json()`; `LockState`, `ObjState` string enums. Consumed by every later task.

- [ ] **Step 1: Write the failing test**

```python
# interfaces/garuda_interfaces/tests/test_tracked_objects.py
from garuda_interfaces.tracked_objects import (
    TrackedObject, TrackedObjects, LockCommand, LockState, ObjState)


def test_tracked_objects_json_roundtrip():
    o = TrackedObject(track_id=7, cls=0, cls_name="person", conf=0.91,
                      bbox=[10.0, 20.0, 30.0, 40.0], age=5, state=ObjState.CONFIRMED)
    assert o.center == [25.0, 40.0]
    msg = TrackedObjects(t_capture_ns=123, frame_w=640, frame_h=480, objects=[o],
                         locked_id=7, lock_state=LockState.LOCKED, seq=2)
    back = TrackedObjects.from_json(msg.to_json())
    assert back == msg
    assert back.objects[0].cls_name == "person"


def test_empty_and_lock_command():
    m = TrackedObjects(t_capture_ns=0, frame_w=640, frame_h=480, objects=[],
                       locked_id=-1, lock_state=LockState.SEARCHING, seq=1)
    assert TrackedObjects.from_json(m.to_json()).objects == []
    c = LockCommand.from_json(LockCommand(cmd="lock", x=100, y=50).to_json())
    assert c.cmd == "lock" and c.x == 100 and c.y == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd interfaces/garuda_interfaces && python -m pytest tests/test_tracked_objects.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

```python
# interfaces/garuda_interfaces/tracked_objects.py
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from enum import Enum


class LockState(str, Enum):
    SEARCHING = "SEARCHING"; LOCKED = "LOCKED"; COASTING = "COASTING"; LOST = "LOST"


class ObjState(str, Enum):
    TENTATIVE = "tentative"; CONFIRMED = "confirmed"; COASTING = "coasting"


@dataclass
class TrackedObject:
    track_id: int
    cls: int
    cls_name: str
    conf: float
    bbox: list          # [x, y, w, h] px, top-left origin
    age: int = 0
    state: ObjState = ObjState.TENTATIVE

    @property
    def center(self) -> list:
        return [self.bbox[0] + self.bbox[2] / 2.0, self.bbox[1] + self.bbox[3] / 2.0]


@dataclass
class TrackedObjects:
    t_capture_ns: int
    frame_w: int
    frame_h: int
    objects: list = field(default_factory=list)
    locked_id: int = -1
    lock_state: LockState = LockState.SEARCHING
    seq: int = 0

    def to_json(self) -> bytes:
        d = asdict(self)
        d["lock_state"] = self.lock_state.value
        for od in d["objects"]:
            od["state"] = od["state"].value if isinstance(od["state"], ObjState) else od["state"]
        return json.dumps(d, separators=(",", ":")).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> "TrackedObjects":
        d = json.loads(raw)
        objs = [TrackedObject(**{**o, "state": ObjState(o["state"])}) for o in d["objects"]]
        d["objects"] = objs
        d["lock_state"] = LockState(d["lock_state"])
        return cls(**d)


@dataclass
class LockCommand:
    cmd: str            # "lock" | "lock_id" | "unlock"
    x: int = -1
    y: int = -1
    track_id: int = -1

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":")).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> "LockCommand":
        return cls(**json.loads(raw))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracked_objects.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add interfaces/garuda_interfaces/tracked_objects.py interfaces/garuda_interfaces/tests/test_tracked_objects.py
git commit -m "feat(interfaces): TrackedObjects + LockCommand contract for garuda-track"
```

---

### Task 2: TrackIPC (seqlock mmap, JSON payload)

**Files:**
- Create: `services/garuda-track/track_ipc.py`
- Test: `services/garuda-track/tests/test_track_ipc.py`

**Interfaces:**
- Consumes: `TrackedObjects` (Task 1).
- Produces: `TrackIPC(path, create, max_bytes=65536)` with `.write(msg: TrackedObjects)` and `.read_latest() -> TrackedObjects | None`. Generalizes `services/garuda-mask/mask_ipc.py` (read it first): header `[seq u64][len u32]`, payload = `TrackedObjects.to_json()`; payload written first, seq last; reader retries on seq mismatch.

- [ ] **Step 1: Write the failing test**

```python
# services/garuda-track/tests/test_track_ipc.py
import os, tempfile
from garuda_interfaces.tracked_objects import TrackedObjects, TrackedObject, LockState, ObjState
from track_ipc import TrackIPC


def _msg(seq):
    return TrackedObjects(t_capture_ns=seq, frame_w=640, frame_h=480,
        objects=[TrackedObject(1, 0, "person", 0.9, [1, 2, 3, 4], 1, ObjState.CONFIRMED)],
        locked_id=1, lock_state=LockState.LOCKED, seq=seq)


def test_write_read_roundtrip(tmp_path):
    p = str(tmp_path / "t.ipc")
    w = TrackIPC(p, create=True); r = TrackIPC(p, create=False)
    assert r.read_latest() is None            # nothing written yet
    w.write(_msg(5))
    got = r.read_latest()
    assert got.seq == 5 and got.objects[0].track_id == 1
    w.write(_msg(6))
    assert r.read_latest().seq == 6           # latest-wins
    w.close(); r.close()


def test_oversize_raises(tmp_path):
    w = TrackIPC(str(tmp_path / "s.ipc"), create=True, max_bytes=64)
    import pytest
    big = TrackedObjects(0, 640, 480, [TrackedObject(i, 0, "x", 0.5, [0, 0, 1, 1]) for i in range(50)], -1, LockState.SEARCHING, 1)
    with pytest.raises(ValueError):
        w.write(big)
    w.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/garuda-track && python -m pytest tests/test_track_ipc.py -v` — Expected: FAIL (no module).

- [ ] **Step 3: Write minimal implementation**

```python
# services/garuda-track/track_ipc.py
from __future__ import annotations
import mmap, struct
from garuda_interfaces.tracked_objects import TrackedObjects

_HDR = struct.Struct("<QI")   # seq, payload_len


class TrackIPC:
    def __init__(self, path: str, create: bool, max_bytes: int = 65536):
        self.path = str(path); self.size = _HDR.size + int(max_bytes); self.cap = int(max_bytes)
        if create:
            with open(self.path, "wb") as f:
                f.write(b"\x00" * self.size)
        self.f = open(self.path, "r+b")
        self.mm = mmap.mmap(self.f.fileno(), self.size)

    def write(self, msg: TrackedObjects) -> None:
        payload = msg.to_json()
        if len(payload) > self.cap:
            raise ValueError(f"payload {len(payload)} > cap {self.cap}")
        self.mm[_HDR.size:_HDR.size + len(payload)] = payload         # payload first
        _HDR.pack_into(self.mm, 0, int(msg.seq), len(payload))        # seq last (release)

    def read_latest(self):
        for _ in range(8):
            seq1, n = _HDR.unpack_from(self.mm, 0)
            if seq1 == 0:
                return None
            payload = bytes(self.mm[_HDR.size:_HDR.size + n])
            seq2, _ = _HDR.unpack_from(self.mm, 0)
            if seq1 == seq2:
                return TrackedObjects.from_json(payload)
        return None

    def close(self):
        self.mm.close(); self.f.close()
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_track_ipc.py -v` (ensure `interfaces/garuda_interfaces` and this dir are importable; add a `conftest.py` that inserts both on `sys.path`). Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(track): TrackIPC seqlock mmap for TrackedObjects"`

---

### Task 3: ByteTrack (vendored, dependency-free)

**Files:**
- Create: `services/garuda-track/bytetrack.py`
- Test: `services/garuda-track/tests/test_bytetrack.py`

**Interfaces:**
- Produces: `ByteTracker(high_thr=0.5, low_thr=0.1, iou_thr=0.3, max_lost=30, min_hits=3)`; `.update(dets) -> list[Track]` where `dets` is a list of `(bbox[x,y,w,h], cls, conf)` and `Track` has `.track_id, .bbox, .cls, .conf, .age, .time_since_update, .hits, .confirmed`. Linear-motion (last-velocity) prediction, greedy two-stage IoU association (no scipy).

- [ ] **Step 1: Write the failing test**

```python
# services/garuda-track/tests/test_bytetrack.py
from bytetrack import ByteTracker


def _step(tr, boxes, conf=0.9):
    return tr.update([(b, 0, conf) for b in boxes])


def test_single_object_keeps_id():
    tr = ByteTracker(min_hits=1)
    ids = []
    for x in range(0, 50, 10):           # object drifting right
        t = _step(tr, [[x, 0, 20, 20]])
        ids.append(t[0].track_id)
    assert len(set(ids)) == 1            # same id throughout


def test_two_objects_distinct_ids():
    tr = ByteTracker(min_hits=1)
    _step(tr, [[0, 0, 20, 20], [100, 0, 20, 20]])
    t = _step(tr, [[10, 0, 20, 20], [110, 0, 20, 20]])
    assert len({x.track_id for x in t}) == 2


def test_low_score_second_stage_recovers():
    tr = ByteTracker(min_hits=1, high_thr=0.5, low_thr=0.1)
    tr.update([([0, 0, 20, 20], 0, 0.9)])
    # next frame the same object is only weakly detected -> low-score stage keeps its id
    t = tr.update([([5, 0, 20, 20], 0, 0.2)])
    assert len(t) == 1 and t[0].track_id == 1


def test_track_dies_after_max_lost():
    tr = ByteTracker(min_hits=1, max_lost=2)
    tr.update([([0, 0, 20, 20], 0, 0.9)])
    for _ in range(4):
        out = tr.update([])              # no detections
    assert out == []                     # removed after max_lost
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_bytetrack.py -v` — Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# services/garuda-track/bytetrack.py  — lean ByteTrack: linear motion + two-stage greedy IoU (no scipy)
from __future__ import annotations
import numpy as np


def _iou(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


class Track:
    _next = 1

    def __init__(self, bbox, cls, conf, min_hits):
        self.track_id = Track._next; Track._next += 1
        self.bbox = list(map(float, bbox)); self.cls = cls; self.conf = conf
        self.vel = [0.0, 0.0]                 # velocity of top-left
        self.age = 1; self.hits = 1; self.time_since_update = 0; self._min_hits = min_hits

    @property
    def confirmed(self):
        return self.hits >= self._min_hits

    def predict(self):
        self.bbox[0] += self.vel[0]; self.bbox[1] += self.vel[1]
        self.age += 1; self.time_since_update += 1

    def update(self, bbox, cls, conf):
        nx, ny = float(bbox[0]), float(bbox[1])
        self.vel = [nx - self.bbox[0], ny - self.bbox[1]]
        self.bbox = [nx, ny, float(bbox[2]), float(bbox[3])]
        self.cls = cls; self.conf = conf; self.hits += 1; self.time_since_update = 0


def _greedy_match(tracks, dets, iou_thr):
    """Return (matches[(ti,di)], unmatched_tracks, unmatched_dets) by descending IoU, greedy."""
    pairs = []
    for ti, t in enumerate(tracks):
        for di, d in enumerate(dets):
            v = _iou(t.bbox, d[0])
            if v >= iou_thr:
                pairs.append((v, ti, di))
    pairs.sort(reverse=True)
    ut, ud = set(range(len(tracks))), set(range(len(dets)))
    matches = []
    for _, ti, di in pairs:
        if ti in ut and di in ud:
            matches.append((ti, di)); ut.discard(ti); ud.discard(di)
    return matches, ut, ud


class ByteTracker:
    def __init__(self, high_thr=0.5, low_thr=0.1, iou_thr=0.3, max_lost=30, min_hits=3):
        self.high_thr, self.low_thr, self.iou_thr = high_thr, low_thr, iou_thr
        self.max_lost, self.min_hits = max_lost, min_hits
        self.tracks: list[Track] = []

    def update(self, dets):
        for t in self.tracks:
            t.predict()
        high = [d for d in dets if d[2] >= self.high_thr]
        low = [d for d in dets if self.low_thr <= d[2] < self.high_thr]
        # stage 1: high-score dets vs all tracks
        m1, ut, ud = _greedy_match(self.tracks, high, self.iou_thr)
        for ti, di in m1:
            self.tracks[ti].update(*high[di][:1], high[di][1], high[di][2])
        # stage 2: low-score dets vs still-unmatched tracks (ByteTrack's key idea)
        rem_tracks = [self.tracks[i] for i in ut]
        m2, ut2, _ = _greedy_match(rem_tracks, low, self.iou_thr)
        matched_rem = set()
        for ti, di in m2:
            rem_tracks[ti].update(*low[di][:1], low[di][1], low[di][2]); matched_rem.add(ti)
        # births: unmatched high-score dets -> new tracks
        for di in ud:
            b, c, s = high[di]
            self.tracks.append(Track(b, c, s, self.min_hits))
        # cull tracks lost too long
        self.tracks = [t for t in self.tracks if t.time_since_update <= self.max_lost]
        return [t for t in self.tracks if t.time_since_update == 0 and t.confirmed]
```

Note: `Track.update(*high[di][:1], ...)` passes the bbox positionally — adjust to `self.tracks[ti].update(high[di][0], high[di][1], high[di][2])` if clearer; keep behavior identical to the tests.

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_bytetrack.py -v`. Reset `Track._next = 1` in a fixture between tests so ids are deterministic. Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(track): vendored lean ByteTrack (linear motion + two-stage greedy IoU, MIT)"`

---

### Task 4: Lock FSM + click selection

**Files:**
- Create: `services/garuda-track/lock_fsm.py`
- Test: `services/garuda-track/tests/test_lock_fsm.py`

**Interfaces:**
- Consumes: `Track` list (Task 3), `LockCommand` (Task 1).
- Produces: `select_by_point(tracks, x, y) -> int` (top-most bbox containing the point, else -1); `LockFSM(n_coast=15)` with `.step(tracks, cmd) -> (locked_id, LockState)`.

- [ ] **Step 1: Write the failing test**

```python
# services/garuda-track/tests/test_lock_fsm.py
from lock_fsm import LockFSM, select_by_point
from garuda_interfaces.tracked_objects import LockCommand, LockState


class T:
    def __init__(self, tid, bbox): self.track_id = tid; self.bbox = bbox


def test_select_by_point_topmost():
    ts = [T(1, [0, 0, 100, 100]), T(2, [40, 40, 20, 20])]   # 2 is on top (later)
    assert select_by_point(ts, 50, 50) == 2
    assert select_by_point(ts, 5, 5) == 1
    assert select_by_point(ts, 500, 500) == -1


def test_lock_coast_lost_cycle():
    fsm = LockFSM(n_coast=2)
    ts = [T(1, [0, 0, 10, 10])]
    assert fsm.step(ts, None) == (-1, LockState.SEARCHING)
    assert fsm.step(ts, LockCommand("lock", x=5, y=5)) == (1, LockState.LOCKED)
    assert fsm.step([], None) == (1, LockState.COASTING)       # missing 1 frame
    assert fsm.step([], None) == (1, LockState.COASTING)       # missing 2 (== n_coast)
    assert fsm.step([], None) == (-1, LockState.LOST)          # exceeded -> drop
    assert fsm.step(ts, None) == (-1, LockState.SEARCHING)     # next frame searching


def test_unlock_and_reacquire():
    fsm = LockFSM(n_coast=5)
    ts = [T(1, [0, 0, 10, 10])]
    fsm.step(ts, LockCommand("lock", x=5, y=5))
    assert fsm.step(ts, LockCommand("unlock")) == (-1, LockState.SEARCHING)
    fsm.step(ts, LockCommand("lock_id", track_id=1))
    assert fsm.step([], None) == (1, LockState.COASTING)       # id gone -> coast, not lost
    assert fsm.step(ts, None) == (1, LockState.LOCKED)         # reappears -> re-locked
```

- [ ] **Step 2: Run to verify fail** — Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# services/garuda-track/lock_fsm.py
from __future__ import annotations
from garuda_interfaces.tracked_objects import LockState


def _contains(bbox, x, y):
    bx, by, bw, bh = bbox
    return bx <= x <= bx + bw and by <= y <= by + bh


def select_by_point(tracks, x, y) -> int:
    for t in reversed(tracks):           # later tracks drawn on top -> reverse = top-most first
        if _contains(t.bbox, x, y):
            return t.track_id
    return -1


class LockFSM:
    def __init__(self, n_coast: int = 15):
        self.n_coast = n_coast; self.locked_id = -1; self.missing = 0
        self.state = LockState.SEARCHING

    def step(self, tracks, cmd):
        ids = {t.track_id for t in tracks}
        if cmd is not None:
            if cmd.cmd == "unlock":
                self.locked_id = -1; self.missing = 0; self.state = LockState.SEARCHING
            elif cmd.cmd == "lock":
                sel = select_by_point(tracks, cmd.x, cmd.y)
                if sel != -1:
                    self.locked_id = sel; self.missing = 0; self.state = LockState.LOCKED
            elif cmd.cmd == "lock_id" and cmd.track_id in ids:
                self.locked_id = cmd.track_id; self.missing = 0; self.state = LockState.LOCKED
        if self.locked_id != -1:
            if self.locked_id in ids:
                self.missing = 0; self.state = LockState.LOCKED
            else:
                self.missing += 1
                if self.missing > self.n_coast:
                    self.locked_id = -1; self.missing = 0; self.state = LockState.LOST
                else:
                    self.state = LockState.COASTING
        elif self.state != LockState.LOST:
            self.state = LockState.SEARCHING
        out = (self.locked_id, self.state)
        if self.state == LockState.LOST:     # LOST is a one-frame edge; next frame is SEARCHING
            self.state = LockState.SEARCHING
        return out
```

- [ ] **Step 4: Run to verify pass** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(track): lock FSM + click-to-track selection"`

---

### Task 5: Lock command channel (UDS datagram)

**Files:**
- Create: `services/garuda-track/cmd_channel.py`
- Test: `services/garuda-track/tests/test_cmd_channel.py`

**Interfaces:**
- Produces: `CmdServer(path)` with non-blocking `.poll() -> list[LockCommand]` (drains queued datagrams); `CmdClient(path)` with `.send(LockCommand)`. UDS `SOCK_DGRAM`, each command delivered once (unlike latest-wins IPC — a lock click must not repeat every frame).

- [ ] **Step 1: Write the failing test**

```python
# services/garuda-track/tests/test_cmd_channel.py
from cmd_channel import CmdServer, CmdClient
from garuda_interfaces.tracked_objects import LockCommand


def test_send_poll(tmp_path):
    sock = str(tmp_path / "cmd.sock")
    srv = CmdServer(sock)
    assert srv.poll() == []                      # non-blocking, empty
    cli = CmdClient(sock)
    cli.send(LockCommand("lock", x=1, y=2))
    cli.send(LockCommand("unlock"))
    got = srv.poll()
    assert [c.cmd for c in got] == ["lock", "unlock"]
    assert srv.poll() == []                       # consumed once
    srv.close()
```

- [ ] **Step 2: Run to verify fail** — Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# services/garuda-track/cmd_channel.py
from __future__ import annotations
import os, socket
from garuda_interfaces.tracked_objects import LockCommand


class CmdServer:
    def __init__(self, path: str):
        self.path = str(path)
        try: os.unlink(self.path)
        except FileNotFoundError: pass
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.s.bind(self.path); self.s.setblocking(False)

    def poll(self):
        out = []
        while True:
            try:
                data = self.s.recv(4096)
            except BlockingIOError:
                break
            out.append(LockCommand.from_json(data))
        return out

    def close(self):
        self.s.close()
        try: os.unlink(self.path)
        except FileNotFoundError: pass


class CmdClient:
    def __init__(self, path: str):
        self.path = str(path); self.s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

    def send(self, cmd: LockCommand):
        self.s.sendto(cmd.to_json(), self.path)
```

- [ ] **Step 4: Run to verify pass** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(track): UDS datagram lock-command channel"`

---

### Task 6: Detector + track_service main loop

**Files:**
- Create: `services/garuda-track/detector.py`, `services/garuda-track/track_service.py`
- Test: `services/garuda-track/tests/test_track_service_offline.py`

**Interfaces:**
- Consumes: `garuda-camera` backend (`CamArrayBackend`/CM3 capture — read `hosts/pi5/camarray_record.py` + `services/garuda-camera` for `configure`/`start(on_frame)`), the Hailo YOLO decode from `services/garuda-inference` (read it for the `hailonet` infer + box/NMS decode; reuse, do not re-implement), Tasks 1–5.
- Produces: `Detector` protocol (`.detect(frame_bgr) -> list[(bbox, cls, conf)]`), `HailoYoloDetector` (YOLOv8m @640, COCO names), `FakeDetector(script)` for tests; `TrackService` that runs capture→detect→ByteTracker→LockFSM(cmd)→TrackIPC.write, and an offline `run_frames(frames)` path for deterministic testing.

- [ ] **Step 1: Write the failing test** — offline, no hardware, `FakeDetector` returns scripted detections; assert the IPC gets a stable locked id.

```python
# services/garuda-track/tests/test_track_service_offline.py
import numpy as np
from detector import FakeDetector
from track_service import TrackService
from garuda_interfaces.tracked_objects import LockCommand, LockState


def test_offline_lock_persists(tmp_path):
    # object drifts right across 6 frames; lock it on frame 1 by clicking its center
    script = [[([x, 40, 20, 20], 0, 0.9)] for x in range(0, 60, 10)]
    svc = TrackService(detector=FakeDetector(script), ipc_path=str(tmp_path / "t.ipc"),
                       cmd_path=str(tmp_path / "c.sock"), min_hits=1)
    frames = [np.zeros((480, 640, 3), np.uint8) for _ in script]
    svc.step(frames[0])                                   # establish track
    svc.inject_cmd(LockCommand("lock", x=10, y=50))       # click on the object
    msgs = [svc.step(f) for f in frames[1:]]
    assert all(m.locked_id == msgs[0].locked_id for m in msgs)
    assert msgs[-1].lock_state == LockState.LOCKED
    svc.close()
```

- [ ] **Step 2: Run to verify fail** — Expected: FAIL.

- [ ] **Step 3: Write minimal implementation** — `detector.py`: `FakeDetector` (pops scripted lists) + a `HailoYoloDetector` that wraps the existing `garuda-inference` YOLO decode (guarded import; only used live). `track_service.py`: `TrackService` holding a `ByteTracker`, `LockFSM`, `CmdServer`, `TrackIPC`; `.step(frame)` = detect → tracker.update → (drain cmds or injected) → fsm.step → build `TrackedObjects` (seq++) → ipc.write → return it. `inject_cmd()` for tests; live loop drains `CmdServer.poll()`. Provide a `main()` that builds `HailoYoloDetector` + `garuda-camera` `on_frame` callback calling `.step`. Keep all Hailo/camera imports inside `main()`/`HailoYoloDetector` so the offline test imports nothing hardware-bound.

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_track_service_offline.py -v` — Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(track): detector abstraction + TrackService main loop (offline-testable)"`

---

### Task 7: Portal overlay + click-to-lock

**Files:**
- Create: `services/garuda-portal/track_overlay.py`
- Modify: `services/garuda-portal/portal.py` (register the route)
- Test: `services/garuda-portal/tests/test_track_overlay.py`

**Interfaces:**
- Consumes: `TrackIPC.read_latest()` (Task 2), `CmdClient` (Task 5), the portal's `camera_stream.py` MJPEG pattern (read it and `hosts/pi5/camarray_focus.py`).
- Produces: `draw_overlay(frame_bgr, tracked: TrackedObjects) -> frame` (boxes; locked one highlighted + label); a route serving the MJPEG overlay stream; a `POST /track/lock` handler mapping `{x,y}` (display coords) → frame coords → `CmdClient.send(LockCommand("lock", x, y))`, and `/track/unlock`.

- [ ] **Step 1: Write the failing test** — pure-function tests (no server): `draw_overlay` returns a same-shape image and draws N boxes; the display→frame coordinate mapping is correct for a known scale.

```python
# services/garuda-portal/tests/test_track_overlay.py
import numpy as np
from track_overlay import draw_overlay, display_to_frame
from garuda_interfaces.tracked_objects import TrackedObjects, TrackedObject, LockState, ObjState


def test_draw_overlay_shape_and_marks():
    f = np.zeros((480, 640, 3), np.uint8)
    m = TrackedObjects(0, 640, 480,
        [TrackedObject(1, 0, "person", 0.9, [10, 10, 50, 50], 3, ObjState.CONFIRMED)],
        locked_id=1, lock_state=LockState.LOCKED, seq=1)
    out = draw_overlay(f.copy(), m)
    assert out.shape == f.shape and out.sum() > 0        # something was drawn


def test_display_to_frame_mapping():
    # display 1280x960 showing a 640x480 frame -> click at (640,480) maps to (320,240)
    assert display_to_frame(640, 480, disp_w=1280, disp_h=960, frame_w=640, frame_h=480) == (320, 240)
```

- [ ] **Step 2: Run to verify fail** — Expected: FAIL.
- [ ] **Step 3: Write minimal implementation** — `draw_overlay` (OpenCV rectangles/labels; locked in accent color + thicker), `display_to_frame` (scale), the MJPEG route (reuse `camera_stream`/`camarray_focus` generator, compositing `draw_overlay(frame, TrackIPC.read_latest())`), and the lock/unlock POST handlers using `CmdClient`. Register routes in `portal.py`.
- [ ] **Step 4: Run to verify pass** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(portal): live track overlay + click-to-lock"`

---

### Task 8: Run script + bench harness

**Files:**
- Create: `hosts/pi5/run_track.sh`, `hosts/pi5/track_bench.py`
- Test: `services/garuda-track/tests/test_run_track_idempotent.py` (lint the script for the depth-stop guard + no unguarded appends)

**Interfaces:**
- Consumes: the full service (Tasks 1–7).
- Produces: `run_track.sh` — idempotent: `sudo systemctl stop garuda-depth` (check-then-act), free the camera/NPU, launch `track_service` + portal, trap-cleanup on exit. `track_bench.py` — runs the live service for N seconds, samples fps + CPU + `vcgencmd get_throttled` + temp, prints a benchlog-ready block.

- [ ] **Step 1: Write the failing test** — assert `run_track.sh` contains a `garuda-depth` stop and uses no unguarded `>>` appends to boot files (grep-based lint).
- [ ] **Step 2: Run to verify fail** — Expected: FAIL.
- [ ] **Step 3: Write minimal implementation** — the script + harness.
- [ ] **Step 4: Run to verify pass** — Expected: PASS.
- [ ] **Step 5: Bench run + record** — on the bench: stop depth, run `track_bench.py`, capture fps/CPU/throttle/temp, add a dated `bench/benchlog.md` entry (numbers are sacred). Commit: `git commit -m "feat(track): idempotent run script + fps/thermal bench harness; benchlog S1 numbers"`.

---

## Self-Review

- **Spec coverage:** contract (T1), IPC (T2), tracker (T3), lock FSM + click (T4), command channel (T5), detector+service (T6), portal overlay+click (T7), run+bench (T8) — covers every S1 success criterion in the design spec.
- **Placeholder scan:** no "TBD/handle appropriately" in code steps; the only deferred choices (transport, overlay side) were resolved here (UDS datagram; server-side draw).
- **Type consistency:** bbox `[x,y,w,h]` px throughout; `TrackedObject.state` uses `ObjState`; `lock_state` uses `LockState`; `Track.track_id/bbox/confirmed` consumed identically by FSM and service. `select_by_point` returns `int` (-1 sentinel) matching `locked_id`.
- **Deps:** no scipy (greedy IoU); ByteTrack vendored; OpenCV/NumPy already on bench.
