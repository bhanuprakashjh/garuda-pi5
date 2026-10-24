# Portal Tracking Convergence + Detection Upgrades — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the portal's follow view THE tracking UI, backed by garuda-mono's track/distance feed, and close the detection gaps (open-vocab yolo_world_v2s mode; bench-gated 960² yolo26m).

**Architecture:** garuda-mono (:8098) stays the CM3 owner and grows `/api/state` + `/api/events` (SSE) plus live depth-engine and detector switches. The portal subscribes via a new `mono_client.py`, adapts mono tracks to the existing detection-dict schema, and publishes onto its EventBus — `follow_publisher_loop`/`FollowPipeline`/garuda-follow are untouched. The browser tracking tab renders mono's MJPEG with the existing follow overlay plus new distance/cal/engine/prompt HUD.

**Tech Stack:** Python 3 stdlib http.server (both services), HailoRT 5.3.0 python API, vanilla-JS SPA (no framework), pytest.

**Spec:** `docs/superpowers/specs/2026-08-07-portal-tracking-integration-design.md`

## Global Constraints

- Branch: `feat/portal-tracking` in a worktree (create at execution start via superpowers:using-git-worktrees).
- Rule 1: every performance number in docs comes from a harness run on the bench; otherwise `TBD-bench`.
- Compute-idle rule preserved: mono computes only while `/stream` viewers OR `/api/events` subscribers > 0.
- SSE state events throttled to ≤10 Hz.
- Licences: yolo_world_v2s GPL-3.0 and YOLO26 AGPL-3.0 are EVAL-ONLY (`models/` zone, never `services/`); no new pip/apt deps without a licence note in the commit.
- Pi-only tests are marked `@pytest.mark.requires_hw` so x86 CI skips them (pytest.ini already defines the marker pattern used by `hosts/pi5/test_*.py`).
- Pi deploy = `scp` changed file(s) to `garuda@10.42.0.128:~/garuda-sdk-deploy/<same relative path>` then `ssh garuda@10.42.0.128 sudo systemctl restart garuda-mono` (portal files: restart `garuda-portal`).
- The Pi runs the deployed copy from `~/garuda-sdk-deploy`; repo worktree is the source of truth — never edit on the Pi.

---

### Task 1: yolo_world_v2s spike — fetch, parse, model card

**Files:**
- Modify: `models/fetch.sh` (add a fetch case)
- Create: `models/yolo_world_v2s_h10.md`
- Modify: `models/LICENCES.md` (one table row)

**Interfaces:**
- Produces: `models/yolo_world_v2s_h10.md` documenting the HEF's exact input/output tensor names, shapes, and dtypes (from `hailortcli parse-hef`), especially the class-embedding input. Task 7 is written against this card.

- [ ] **Step 1: Add the fetch entry**

In `models/fetch.sh`, after the `scdepthv3` case line, add:

```bash
case " $want " in *" yolo_world_v2s "*) fetch yolo_world_v2s.hef yolo_world_v2s_h10.hef "GPL-3.0, eval-only";; esac
```

and extend the default list: `want="${*:-stereonet fast_depth scdepthv3 yolo_world_v2s}"`.

- [ ] **Step 2: Fetch and inspect on the Pi**

```bash
./models/fetch.sh yolo_world_v2s
scp models/yolo_world_v2s_h10.hef garuda@10.42.0.128:~/garuda-sdk-deploy/models/
ssh garuda@10.42.0.128 'hailortcli parse-hef ~/garuda-sdk-deploy/models/yolo_world_v2s_h10.hef'
```

Expected: input(s) incl. a 640x640x3 UINT8 image tensor; note whether a second input exists for class text embeddings (shape like (1, N, 512)) and every output tensor. Record the verbatim parse-hef output in the card.

- [ ] **Step 3: Write the model card**

`models/yolo_world_v2s_h10.md` — follow `scdepthv3_h10.md`'s structure: licence banner (GPL-3.0 — EVAL-ONLY), source link (https://github.com/AILab-CVC/YOLO-World), zoo HEF URL + zoo numbers (zero-shot mAP 36.4 float / 31.9 HW, 46.6 fps b1 — cite "zoo published"), the parse-hef I/O block, and a **Prompt contract** section: how class embeddings are produced and fed (from Step 2 findings). If the HEF turns out fixed-vocab (no embedding input), state that and record the spec's fallback (prompt-set-at-build-time rebuild path) — Task 7 then implements that variant.

- [ ] **Step 4: Licence row**

Add to `models/LICENCES.md` table: `| yolo_world_v2s | zero-shot detection | GPL-3.0 | ❌ eval-only | yolo_world_v2s_h10.md | prompt-driven open-vocab |`

- [ ] **Step 5: Commit**

```bash
git add models/fetch.sh models/yolo_world_v2s_h10.md models/LICENCES.md
git commit -m "docs(models): yolo_world_v2s spike — fetch entry, I/O contract card (GPL-3.0 eval-only)"
```

---

### Task 2: mono_state — pure snapshot builder (CI-testable)

**Files:**
- Create: `hosts/pi5/mono_state.py`
- Test: `hosts/pi5/test_mono_state.py`

**Interfaces:**
- Produces: `build_snapshot(state: dict, extras: dict) -> dict` returning the versioned wire object below. Tasks 3/5/6 depend on these exact key names.

Wire object (v1):

```python
{"v": 1, "age_ms": int|None,
 "tracks": [{"id": int, "label": str, "conf": float, "x": float, "y": float,
             "w": float, "h": float, "coasting": bool}],
 "lock": {"id": int|None, "class": str, "coasting": bool},
 "distance": {"raw_m": float|None, "est_m": float|None, "valid": bool},
 "depth_engine": str, "detector": {"mode": str, "prompt": list},
 "cal": {"n": int, "fit": str|None},
 "frame": {"width": int, "height": int}}
```

- [ ] **Step 1: Write the failing tests**

`hosts/pi5/test_mono_state.py`:

```python
from mono_state import build_snapshot

STATE = {"class": "person", "raw": 2.5, "t": 100.0, "count": 2,
         "tracks_full": [(3, "person", 10.0, 20.0, 30.0, 40.0, 0.9, False),
                         (7, "chair", 50.0, 60.0, 70.0, 80.0, 0.4, True)]}
EXTRAS = {"lock_id": 3, "lock_coasting": False, "est_m": 2.4, "valid": True,
          "depth_engine": "y26d", "detector_mode": "primary", "prompt": [],
          "cal_n": 0, "fit": None, "age_ms": 120, "frame_w": 1280, "frame_h": 720}

def test_snapshot_shape_and_tracks():
    s = build_snapshot(STATE, EXTRAS)
    assert s["v"] == 1 and s["age_ms"] == 120
    assert s["tracks"][0] == {"id": 3, "label": "person", "conf": 0.9,
                              "x": 10.0, "y": 20.0, "w": 30.0, "h": 40.0, "coasting": False}
    assert s["lock"] == {"id": 3, "class": "person", "coasting": False}
    assert s["distance"] == {"raw_m": 2.5, "est_m": 2.4, "valid": True}
    assert s["frame"] == {"width": 1280, "height": 720}

def test_snapshot_no_lock_no_distance():
    s = build_snapshot({**STATE, "raw": None}, {**EXTRAS, "lock_id": None,
                                                "est_m": None, "valid": False})
    assert s["lock"]["id"] is None
    assert s["distance"] == {"raw_m": None, "est_m": None, "valid": False}

def test_snapshot_tolerates_short_track_tuples():
    # pre-migration 6-tuples (no conf/coasting) must not crash: conf 0.0, coasting False
    s = build_snapshot({**STATE, "tracks_full": [(1, "person", 1.0, 2.0, 3.0, 4.0)]}, EXTRAS)
    assert s["tracks"][0]["conf"] == 0.0 and s["tracks"][0]["coasting"] is False
```

- [ ] **Step 2: Run to verify failure** — `cd hosts/pi5 && python3 -m pytest test_mono_state.py -v` → FAIL (`ModuleNotFoundError: mono_state`).

- [ ] **Step 3: Implement**

`hosts/pi5/mono_state.py`:

```python
"""Pure builders for garuda-mono's wire state (v1). No hailo/cv2 imports — unit-tested on x86 CI.

The snapshot is the single JSON object served by GET /api/state and streamed by
GET /api/events; services/garuda-portal/mono_client.py consumes exactly these keys.
"""


def build_snapshot(state: dict, extras: dict) -> dict:
    tracks = []
    for t in state.get("tracks_full", []):
        tid, label, x, y, w, h = t[:6]
        conf = float(t[6]) if len(t) > 6 else 0.0
        coasting = bool(t[7]) if len(t) > 7 else False
        tracks.append({"id": int(tid), "label": label, "conf": conf,
                       "x": float(x), "y": float(y), "w": float(w), "h": float(h),
                       "coasting": coasting})
    return {
        "v": 1,
        "age_ms": extras.get("age_ms"),
        "tracks": tracks,
        "lock": {"id": extras.get("lock_id"), "class": state.get("class", ""),
                 "coasting": bool(extras.get("lock_coasting", False))},
        "distance": {"raw_m": state.get("raw"), "est_m": extras.get("est_m"),
                     "valid": bool(extras.get("valid", False))},
        "depth_engine": extras.get("depth_engine", ""),
        "detector": {"mode": extras.get("detector_mode", "primary"),
                     "prompt": list(extras.get("prompt", []))},
        "cal": {"n": int(extras.get("cal_n", 0)), "fit": extras.get("fit")},
        "frame": {"width": int(extras.get("frame_w", 0)), "height": int(extras.get("frame_h", 0))},
    }
```

- [ ] **Step 4: Run to verify pass** — same command → 3 PASS.

- [ ] **Step 5: Commit** — `git add hosts/pi5/mono_state.py hosts/pi5/test_mono_state.py && git commit -m "feat(mono): versioned wire-state snapshot builder (pure, CI-tested)"`

---

### Task 3: mono `/api/state` + `/api/events` SSE

**Files:**
- Modify: `hosts/pi5/garuda_mono.py` (loop() tracks_full; do_GET; viewer accounting)

**Interfaces:**
- Consumes: `mono_state.build_snapshot` (Task 2).
- Produces: `GET /api/state` → snapshot JSON (one-shot). `GET /api/events` → SSE, one `data: <snapshot-json>\n\n` per tick at ≤10 Hz. Both count as viewers for the compute-idle rule.

- [ ] **Step 1: Extend tracks_full with conf + coasting**

In `loop()` replace the `tracks_full = ...` line with:

```python
        tracks_full = [(t.track_id, t.cls, t.bbox[0], t.bbox[1], t.bbox[2], t.bbox[3],
                        float(t.conf), t.time_since_update > 0) for t in alive]
```

(`/api/mono_click`'s `for tid, lbl, x, y, w, h in tf:` must become `for tid, lbl, x, y, w, h, *_ in tf:` — 8-tuples now.)

Also capture the lock coast state for the API: after the `ebbox/econf/coasting` assignment add `_state` key in the same `_state.update(...)` call: `lock_coasting=coasting`.

- [ ] **Step 2: Add a snapshot helper + the two endpoints**

Top of file: `from mono_state import build_snapshot`. In `make_handler()`'s class add:

```python
        def _snapshot(self):
            with _lock:
                s = dict(_state)
            est = _est(s["raw"])
            age = int((time.monotonic() - s["t"]) * 1000) if s["t"] else None
            valid = s["raw"] is not None and (age is None or age < RANGE_STALE_MS)
            return build_snapshot(s, {
                "lock_id": _lockid["id"], "lock_coasting": s.get("lock_coasting", False),
                "est_m": est if valid else None, "valid": valid, "age_ms": age,
                "depth_engine": _engine["name"] if "_engine" in globals() else DEPTH_SRC,
                "detector_mode": "primary", "prompt": [],
                "cal_n": len(_cal), "fit": (_fit[0] if _fit else None),
                "frame_w": CAM_W, "frame_h": CAM_H})
```

In `do_GET`, before the 404 branch:

```python
            elif p == "/api/state":
                self._send(200, "application/json", json.dumps(self._snapshot()).encode())
            elif p == "/api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
                with _lock:
                    _viewers["n"] += 1
                try:
                    while True:
                        body = json.dumps(self._snapshot())
                        self.wfile.write(b"data: " + body.encode() + b"\n\n")
                        self.wfile.flush()
                        time.sleep(0.1)                      # <=10 Hz
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with _lock:
                        _viewers["n"] = max(0, _viewers["n"] - 1)
```

(Note `"detector_mode": "primary"` is a constant until Task 7 replaces it with the `_detector` global.)

- [ ] **Step 3: Deploy + verify on the Pi**

```bash
scp hosts/pi5/mono_state.py hosts/pi5/garuda_mono.py garuda@10.42.0.128:~/garuda-sdk-deploy/hosts/pi5/
ssh garuda@10.42.0.128 'sudo systemctl restart garuda-mono && sleep 3 && curl -s localhost:8098/api/state | python3 -m json.tool | head -20 && curl -sN --max-time 2 localhost:8098/api/events | head -4'
```

Expected: `/api/state` returns the v1 snapshot; `/api/events` emits `data: {...}` lines. With an events client attached and NO /stream viewer, `curl -s localhost:8098/api/state` must show `age_ms` decreasing/fresh (compute active — the idle rule counts SSE).

- [ ] **Step 4: Commit** — `git add hosts/pi5/garuda_mono.py && git commit -m "feat(mono): /api/state + /api/events SSE (v1 snapshot, <=10 Hz, viewer-counted)"`

---

### Task 4: mono live depth-engine switch

**Files:**
- Modify: `hosts/pi5/garuda_mono.py` (main(), loop(), `_est`, do_POST)

**Interfaces:**
- Produces: `POST /api/depth_engine?engine=y26d|da` → `{"ok": true, "engine": ...}` (400 on unknown). Snapshot `depth_engine` reflects the live value. Boot default still `GARUDA_MONO_DEPTH`.

- [ ] **Step 1: Both engines resident + `_engine` global**

Replace the module-level `METRIC_DEPTH = DEPTH_SRC != "da"` with `_engine = {"name": DEPTH_SRC}` (keep `DEPTH_SRC` as the boot default). In `_est`, replace `METRIC_DEPTH` with `(_engine["name"] != "da")`. In `main()` replace the `da = ...` conditional with:

```python
    engines = {"y26d": Yolo26Depth(Y26D_HEF, vdevice=vd), "da": DepthAnything(vd)}
    threading.Thread(target=loop, args=(det, engines), daemon=True).start()
```

In `loop(det, engines)` replace the `da.infer(img)` call with `engines[_engine["name"]].infer(img)` and reset the depth map + EMA on switch: track `cur_engine = _engine["name"]` each iteration; when it changes, set `depth = None; _ema["raw"] = None; cur_engine = _engine["name"]` before inferring.

- [ ] **Step 2: The POST route**

In `do_POST`, before the 404 branch:

```python
            elif u.path == "/api/depth_engine":
                eng = q.get("engine", [""])[0]
                if eng not in ("y26d", "da"):
                    self._send(400, "application/json", b'{"ok":false}'); return
                _engine["name"] = eng
                self._send(200, "application/json",
                           json.dumps({"ok": True, "engine": eng}).encode())
```

And in `_snapshot()` (Task 3) the `depth_engine` extra is now simply `_engine["name"]`.

- [ ] **Step 3: Deploy + verify (three models co-resident on the 10H)**

```bash
scp hosts/pi5/garuda_mono.py garuda@10.42.0.128:~/garuda-sdk-deploy/hosts/pi5/
ssh garuda@10.42.0.128 'sudo systemctl restart garuda-mono && sleep 3 \
  && curl -sN --max-time 1 localhost:8098/api/events >/dev/null; \
  curl -s -X POST "localhost:8098/api/depth_engine?engine=da" \
  && curl -s localhost:8098/api/state | python3 -c "import json,sys; print(json.load(sys.stdin)[\"depth_engine\"])" \
  && curl -s -X POST "localhost:8098/api/depth_engine?engine=y26d" \
  && journalctl -u garuda-mono -n 3 --no-pager -o cat'
```

Expected: switch returns ok, snapshot reflects `da` then back to `y26d`, no infer errors in the journal (if VDevice refuses a third configured model, that is a task-blocking finding: record it and fall back to configuring the inactive engine lazily on first switch).

- [ ] **Step 4: Commit** — `git add hosts/pi5/garuda_mono.py && git commit -m "feat(mono): live y26d<->da depth-engine switch, both engines resident"`

---

### Task 5: portal mono_client — SSE consumer + schema adapter

**Files:**
- Create: `services/garuda-portal/mono_client.py`
- Test: `services/garuda-portal/tests/test_mono_client.py`

**Interfaces:**
- Consumes: mono v1 snapshot (Task 2 shape) via `GET /api/events`.
- Produces: `snapshot_to_detections(snap: dict) -> dict` returning `{"detections": [{"class_id": int, "label": str, "confidence": float, "x": float, "y": float, "w": float, "h": float}], "frame": {"width": int, "height": int}}` (exactly the DetectionSet-dict `FollowPipeline.on_detections` consumes). `MonoClient(bus, base_url).start()` daemon thread: publishes `detections` (adapted) and `mono` (raw snapshot) events on the portal EventBus; on connection failure retries every 2 s and publishes `error` events `{"stage": "mono", "detail": ...}` at most once per outage.

- [ ] **Step 1: Write the failing tests**

`services/garuda-portal/tests/test_mono_client.py`:

```python
import json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mono_client import snapshot_to_detections, MonoClient
from events import EventBus

SNAP = {"v": 1, "tracks": [{"id": 3, "label": "person", "conf": 0.9,
                            "x": 10.0, "y": 20.0, "w": 30.0, "h": 40.0, "coasting": False}],
        "lock": {"id": 3, "class": "person", "coasting": False},
        "distance": {"raw_m": 2.5, "est_m": 2.4, "valid": True},
        "depth_engine": "y26d", "detector": {"mode": "primary", "prompt": []},
        "cal": {"n": 0, "fit": None}, "frame": {"width": 1280, "height": 720}, "age_ms": 50}

def test_adapter_maps_tracks_to_detection_dicts():
    d = snapshot_to_detections(SNAP)
    assert d["frame"] == {"width": 1280, "height": 720}
    det = d["detections"][0]
    assert det["label"] == "person" and det["confidence"] == 0.9
    assert (det["x"], det["y"], det["w"], det["h"]) == (10.0, 20.0, 30.0, 40.0)
    assert isinstance(det["class_id"], int)

def test_adapter_unknown_label_gets_minus_one():
    snap = dict(SNAP, tracks=[dict(SNAP["tracks"][0], label="pen")])
    assert snapshot_to_detections(snap)["detections"][0]["class_id"] == -1

def _sse_server(payloads):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for p in payloads:
                self.wfile.write(b"data: " + json.dumps(p).encode() + b"\n\n")
            time.sleep(0.5)   # hold the connection open briefly
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def test_client_publishes_detections_and_mono_events():
    srv = _sse_server([SNAP])
    bus = EventBus(); q = bus.subscribe()
    MonoClient(bus, f"http://127.0.0.1:{srv.server_address[1]}").start()
    kinds = {}
    deadline = time.time() + 3
    while time.time() < deadline and not {"detections", "mono"} <= set(kinds):
        try: ev = q.get(timeout=0.5)
        except Exception: continue
        kinds[ev["kind"]] = ev["data"]
    srv.shutdown()
    assert "mono" in kinds and kinds["mono"]["lock"]["id"] == 3
    assert "detections" in kinds and kinds["detections"]["detections"][0]["label"] == "person"
```

- [ ] **Step 2: Run to verify failure** — `cd services/garuda-portal && python3 -m pytest tests/test_mono_client.py -v` → FAIL (`ModuleNotFoundError: mono_client`).

- [ ] **Step 3: Implement**

`services/garuda-portal/mono_client.py`:

```python
"""MonoClient — the portal's subscription onto garuda-mono's state feed.

Reads GET /api/events (SSE, v1 snapshots — hosts/pi5/mono_state.py is the
producer) and republishes each tick on the portal EventBus as TWO events:
`detections` (adapted to the DetectionSet dict FollowPipeline.on_detections
already consumes, so follow/drone-dir works unchanged) and `mono` (the raw
snapshot, for the tracking HUD: distance/lock/cal/engine/detector).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

# COCO index for class_id mapping; labels outside COCO (world-mode prompts) -> -1.
COCO = ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
        "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
        "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
        "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
        "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
        "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
        "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
        "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
        "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
        "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
        "toothbrush"]
_COCO_IDX = {c: i for i, c in enumerate(COCO)}


def snapshot_to_detections(snap: dict) -> dict:
    dets = [{"class_id": _COCO_IDX.get(t.get("label"), -1), "label": t.get("label", ""),
             "confidence": float(t.get("conf", 0.0)), "x": float(t.get("x", 0.0)),
             "y": float(t.get("y", 0.0)), "w": float(t.get("w", 0.0)), "h": float(t.get("h", 0.0))}
            for t in snap.get("tracks", [])]
    frame = snap.get("frame") or {}
    return {"detections": dets,
            "frame": {"width": frame.get("width") or 1, "height": frame.get("height") or 1}}


class MonoClient:
    def __init__(self, bus, base_url: str, retry_s: float = 2.0):
        self.bus = bus
        self.base_url = base_url.rstrip("/")
        self.retry_s = retry_s
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        reported_down = False
        while True:
            try:
                resp = urllib.request.urlopen(self.base_url + "/api/events", timeout=10)
                reported_down = False
                for line in resp:
                    if not line.startswith(b"data: "):
                        continue
                    try:
                        snap = json.loads(line[6:])
                    except ValueError:
                        continue                     # malformed tick: drop, keep the stream
                    self.bus.publish("mono", snap)
                    self.bus.publish("detections", snapshot_to_detections(snap))
            except Exception as e:
                if not reported_down:
                    self.bus.publish("error", {"stage": "mono", "detail": str(e)})
                    reported_down = True
            time.sleep(self.retry_s)
```

- [ ] **Step 4: Run to verify pass** — same command → 3 PASS. Also run the whole portal suite: `python3 -m pytest tests/ -v` → no regressions.

- [ ] **Step 5: Commit** — `git add services/garuda-portal/mono_client.py services/garuda-portal/tests/test_mono_client.py && git commit -m "feat(portal): MonoClient — mono SSE -> bus (detections + mono events)"`

---

### Task 6: portal wiring + converged tracking view

**Files:**
- Modify: `services/garuda-portal/portal.py` (main(): start MonoClient; no other changes)
- Modify: `services/garuda-portal/static/app.js` (tracking tab source + HUD; remove mono tab)
- Modify: `services/garuda-portal/static/index.html` (HUD elements; remove mono panel)

**Interfaces:**
- Consumes: `MonoClient` (Task 5); `mono` SSE events on the portal's existing `/api/events`; mono's direct HTTP API on :8098 (CORS `*` already set by mono).
- Produces: the converged Tracking tab. Click-to-lock = existing overlay pin POST **plus** `POST :8098/api/mono_click?x=&y=` (both derived from the same click point). HUD ids: `#trk-dist`, `#trk-chart`, `#trk-engine`, `#trk-class`, `#trk-prompt`, `#trk-cal-truth`, `#trk-cal-add`, `#trk-cal-reset`.

- [ ] **Step 1: Wire MonoClient in `portal.py` main()**

After `follow_pipeline = FollowPipeline()` and the bus creation, add:

```python
    from mono_client import MonoClient
    MonoClient(bus, os.environ.get("GARUDA_MONO_URL", "http://127.0.0.1:8098")).start()
```

(`import os` if not present.) The existing `follow_publisher_loop` turns the published `detections` events into `follow` events — no change there. The InferenceManager CM3 child path is now unused by tracking; leave the class and its tests in place (other tabs), but the tracking view no longer calls `inference` mode.

- [ ] **Step 2: Point the tracking view at mono's stream + forward clicks**

In `app.js`: the follow overlay currently draws over the portal's `/api/stream` `<img>`. Change the tracking panel's image source to `` `${location.protocol}//${location.hostname}:8098/stream` `` (same lazy load/unload pattern the old mono tab used — set `src` on panel enter, remove on leave, so mono idles). In the overlay's click handler (`pointInBbox` flow), after the existing pin POST add:

```javascript
  const mbase = `${location.protocol}//${location.hostname}:8098`;
  fetch(`${mbase}/api/mono_click?x=${Math.round(nx * imgW)}&y=${Math.round(ny * imgH)}`,
        { method: "POST" }).catch(() => {});
```

where `nx, ny` are the normalized click coords the overlay already computes and `imgW/imgH` come from the `mono` event's `frame` (fallback 1280×720).

- [ ] **Step 3: HUD — distance, chart, controls**

`index.html` tracking panel: add a HUD row (`<span id=trk-dist>`, `<canvas id=trk-chart height=70>`, `<select id=trk-engine><option>y26d</option><option>da</option></select>`, `<select id=trk-class>` (COCO, "person" default), `<input id=trk-prompt placeholder="open-vocab: pen, keys...">`, `<input id=trk-cal-truth type=number step=.05>`, `<button id=trk-cal-add>cal</button>`, `<button id=trk-cal-reset>reset</button>`). Delete the mono panel markup.

`app.js`: subscribe to `mono` events in the existing EventSource dispatch (`case "mono": onMono(ev.data)`), and:

```javascript
const _trkHist = [];
function onMono(s) {
  const d = s.distance || {};
  setText("trk-dist", d.valid && d.est_m != null ? d.est_m.toFixed(2) + " m"
                                                 : (s.lock && s.lock.id != null ? "locked…" : "—"));
  _trkHist.push({ t: Date.now(), v: d.valid && d.est_m != null ? d.est_m : null });
  drawTrkChart();
  const eng = document.getElementById("trk-engine");
  if (eng && document.activeElement !== eng) eng.value = s.depth_engine || "y26d";
}
```

`drawTrkChart()` = the 60 s sparkline from mono's PAGE chart (same algorithm: shift >60 s, min/max + pad, polyline, labels) rendered into `#trk-chart`. Wire the controls (all direct to mono, CORS is open):

```javascript
function wireTracking() {
  const mbase = `${location.protocol}//${location.hostname}:8098`;
  const on = (id, ev, fn) => { const el = document.getElementById(id); if (el) el.addEventListener(ev, fn); };
  on("trk-engine", "change", (e) =>
    fetch(`${mbase}/api/depth_engine?engine=${e.target.value}`, { method: "POST" }).catch(() => {}));
  on("trk-class", "change", (e) =>
    fetch(`${mbase}/api/mono_target?class=${encodeURIComponent(e.target.value)}`, { method: "POST" }).catch(() => {}));
  on("trk-prompt", "change", (e) => {
    const words = e.target.value.split(",").map(s => s.trim()).filter(Boolean);
    fetch(`${mbase}/api/detector?mode=${words.length ? "world" : "primary"}` +
          words.map(w => `&prompt=${encodeURIComponent(w)}`).join(""), { method: "POST" }).catch(() => {});
  });
  on("trk-cal-add", "click", () => {
    const v = parseFloat(document.getElementById("trk-cal-truth").value);
    if (v > 0) fetch(`${mbase}/api/mono_calibrate?dist=${v}`, { method: "POST" }).catch(() => {});
  });
  on("trk-cal-reset", "click", () =>
    fetch(`${mbase}/api/mono_calibrate_reset`, { method: "POST" }).catch(() => {}));
}
```

Remove `startMonoPoll/stopMonoPoll/wireMono` and the `"mono"` entry from `PANELS`. Offline handling: on `error` events with `stage === "mono"`, show the existing banner mechanism with "mono offline — retrying".

- [ ] **Step 4: Deploy + verify end-to-end on the bench**

```bash
scp services/garuda-portal/portal.py services/garuda-portal/mono_client.py garuda@10.42.0.128:/opt/garuda/garuda-portal/
scp services/garuda-portal/static/*.{js,html,css} garuda@10.42.0.128:/opt/garuda/garuda-portal/static/
ssh garuda@10.42.0.128 'sudo systemctl restart garuda-portal'
```

Open `http://10.42.0.128:8080/` → Tracking tab: mono video shows, boxes overlay, click a person → reticle + mono lock, distance HUD live, chart scrolling, engine toggle flips snapshot `depth_engine`, `systemctl stop garuda-mono` → offline banner; `start` → recovers.

- [ ] **Step 5: Commit** — `git add services/garuda-portal/ && git commit -m "feat(portal): converged tracking view — mono-backed overlay, distance HUD, engine/cal/prompt controls"`

---

### Task 7: mono world-mode detector (spike-gated)

**Files:**
- Create: `hosts/pi5/hailo_yolo_world.py`
- Modify: `hosts/pi5/garuda_mono.py` (detector switch + `/api/detector`)

**Interfaces:**
- Consumes: `models/yolo_world_v2s_h10.md` (Task 1 contract) — adjust tensor names to the card before coding.
- Produces: `POST /api/detector?mode=primary|world&prompt=a&prompt=b` → `{"ok": true, "mode": ..., "prompt": [...]}`; world mode detections flow through the SAME ByteTracker/lock path (detection objects expose `.x .y .w .h .label .conf` like `HailoYolo26`'s). Snapshot `detector` reflects live mode/prompt. On any world-path failure at switch time: stay on primary, return `{"ok": false, "reason": ...}`.

- [ ] **Step 1: Text-embedding source.** Per the card: if the HEF takes an embedding input, produce embeddings with the zoo's CLIP text tower (`text_image_retrieval` category) run via onnxruntime on the Pi CPU (`pip: onnxruntime` — MIT licence, note in commit) with the official YOLO-World prompt template; cache per prompt-list. If the card says fixed-vocab: implement `/api/detector` to return `{"ok": false, "reason": "fixed-vocab HEF — rebuild required"}` for world mode and document the rebuild path in the card; the UI already surfaces the failure.

- [ ] **Step 2: Wrapper.** `hosts/pi5/hailo_yolo_world.py` mirroring `hailo_yolo26.py`'s interface (`__init__(hef, vdevice, conf_thresh)`, `.infer(bgr) -> [Det]`, embeddings set via `.set_prompt(labels, embeddings)`); decode per the card's output contract. Verify standalone on a saved frame: `ssh garuda@10.42.0.128 'python3 ~/garuda-sdk-deploy/hosts/pi5/hailo_yolo_world.py ~/garuda-sdk-deploy/models/yolo_world_v2s_h10.hef /tmp/probe.jpg "pen,keys,person"'` prints per-class detections.

- [ ] **Step 3: Switch in `loop()`/`main()`.** `_detector = {"mode": "primary", "prompt": []}` global; `main()` configures both `HailoYolo26` and (lazily, first use) `HailoYoloWorld` on the shared vdevice; `loop()` picks per-frame like the depth-engine switch; `/api/detector` POST validates, computes embeddings (Step 1), swaps, falls back with reason on exception. Snapshot's `detector_mode`/`prompt` extras read `_detector`.

- [ ] **Step 4: Bench check + benchlog.** With world mode + prompt "pen, keys, person": fps from 12 s of `/api/events` ticks, `vcgencmd get_throttled`, journal clean → record measured numbers in `bench/benchlog.md` (rule 1).

- [ ] **Step 5: Commit** — `git add hosts/pi5/hailo_yolo_world.py hosts/pi5/garuda_mono.py bench/benchlog.md && git commit -m "feat(mono): yolo_world_v2s open-vocab detector mode (/api/detector, GPL-3.0 eval-only)"`

---

### Task 8: 960² yolo26m rebuild + bench gate

**Files:**
- Modify: `hosts/pi5/garuda_mono.py` (YOLO path env override, one line)
- Modify: `bench/benchlog.md` (gate numbers)

**Interfaces:**
- Produces: `YOLO = os.environ.get("GARUDA_MONO_YOLO", "/home/garuda/garuda-sdk-deploy/models/yolo26m.hef")` — the 960² HEF ships ONLY by flipping this env in a systemd drop-in after the gate passes.

- [ ] **Step 1: Build on the PC** (venv exporter state: level 2, finetune disabled — same as the depth build):

```bash
cd /media/bhanu1234/Development/hailo-export/work
TMPDIR=../tmp ../venv/bin/yolo export model=yolo26m.pt format=hailo name=hailo10h imgsz=960 \
  data=/media/bhanu1234/Development/hailo-export/datasets/depth-calib.yaml
```

(The calib yaml's label-less images act as backgrounds for the detect loader — calibration reads images only. If the loader rejects the set, fall back to `data=coco128.yaml` and note it.)

- [ ] **Step 2: Deploy + gate.** `scp -r yolo26m_hailo_model garuda@10.42.0.128:~/garuda-sdk-deploy/models/yolo26m960_hailo_model` (rename to avoid clobbering), add drop-in env `GARUDA_MONO_YOLO=.../yolo26m960_hailo_model/yolo26m.hef`, restart, then measure 60 s with a stream viewer attached: processed-frame rate from `/api/events` `age_ms` freshness, `vcgencmd get_throttled`, SoC temp. GATE: sustains ≥15 fps camera rate with depth co-resident AND low throttle bits 0x0. Far-object check: place a small object at the far wall, confirm detection at 960 that 640 missed (qualitative note).

- [ ] **Step 3: Decide + record.** PASS → keep the drop-in, benchlog entry with numbers. FAIL → remove the drop-in (back to 640), benchlog the measured numbers + verdict anyway.

- [ ] **Step 4: Commit** — `git add hosts/pi5/garuda_mono.py bench/benchlog.md && git commit -m "feat(mono): GARUDA_MONO_YOLO override + 960sq gate run (numbers in benchlog)"`

---

### Task 9: close-out — validation sweep + docs

**Files:**
- Modify: `bench/benchlog.md`, `CLAUDE.md` (§7), `docs/superpowers/specs/2026-08-07-portal-tracking-integration-design.md` (status line)

- [ ] **Step 1: Full-suite runs.** x86: `python3 -m pytest services/garuda-portal/tests hosts/pi5 -v` (hw-marked tests skip). Bench: Tracking tab end-to-end (lock, distance, chart, engine flip, world prompt, cal add/reset, mono-down banner + recovery).
- [ ] **Step 2: benchlog.** One consolidated entry: portal-tracking convergence live; measured fps/thermals from Tasks 7/8; red-team note (rule 9): what could make these numbers flattering?
- [ ] **Step 3: CLAUDE.md §7.** New state + next objectives (y26d physical validation still deferred/TBD-bench; auth + non-sudo account remain the pre-Phase-8 gates).
- [ ] **Step 4: Spec status** → `implemented <date>`. Commit: `git add -A && git commit -m "docs: portal-tracking close-out — benchlog, CLAUDE.md resume state"`. Merge to master only on owner OK.
