# garuda-portal V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Browser portal served from the Pi: health overview, live camera+detections, whitelisted settings, guided intrinsics calibration, automated readiness report — per spec `docs/superpowers/specs/2026-07-30-garuda-portal-v1-design.md`.

**Architecture:** Two processes: unprivileged `garuda-portal` (stdlib HTTP: static UI + REST + SSE + MJPEG) and root `garuda-configd` (UNIX-socket whitelist executor). Probes are injectable functions so all logic is testable off-target; shell-outs happen only in live wiring.

**Tech Stack:** Python 3.11+ stdlib (http.server, socketserver, json, subprocess, threading); pytest; OpenCV (`python3-opencv`, calibration only); plain HTML/JS/CSS (no toolchain).

## Global Constraints

- Licence gate: BSD/MIT/Apache/LGPL-dynamic only; new dep `python3-opencv` (BSD) — no GPLv3 (CLAUDE.md §6.6).
- No arming/offboard control anywhere in the portal (spec: "never sends arm/offboard").
- configd accepts ONLY whitelisted ops; everything else rejected AND audit-logged.
- All timestamps monotonic ns, fields end `_ns` (interfaces/README.md rule 3).
- Dev machine tests: `pytest` from repo root must pass without Pi hardware (probes mocked).
- Commit after every task with its test evidence; messages end with the Claude Fable co-author line.
- Bench deploys go to `garuda@10.42.0.128` (key auth, NOPASSWD sudo); services run as transient units during bring-up, systemd unit files land in Task 9.

## File Structure

```
services/garuda-configd/garuda_configd.py     # daemon: socket loop + dispatch
services/garuda-configd/whitelist.py          # op validation + config schema (pure logic)
services/garuda-configd/tests/test_whitelist.py
services/garuda-portal/portal.py              # HTTP server wiring (routes only)
services/garuda-portal/probes.py              # health probes, injectable runner
services/garuda-portal/readiness.py           # check registry + runner (pure logic)
services/garuda-portal/calibration.py         # coverage grid + solve (pure logic + cv2)
services/garuda-portal/configd_client.py      # UNIX socket client
services/garuda-portal/camera_stream.py       # MJPEG child process + frame fanout
services/garuda-portal/static/{index.html,app.js,style.css}
services/garuda-portal/tests/{test_readiness.py,test_probes.py,test_calibration.py}
hosts/pi5/units/{garuda-portal.service,garuda-configd.service}
hosts/pi5/install.sh                          # modify: add python3-opencv, unit install
```

---

### Task 1: configd whitelist + config schema (pure logic)

**Files:**
- Create: `services/garuda-configd/whitelist.py`
- Test: `services/garuda-configd/tests/test_whitelist.py`

**Interfaces:**
- Produces: `validate_request(req: dict) -> tuple[bool, str]`; `CONFIG_SCHEMA: dict[str, callable]`; `apply_config_set(store: dict, key: str, value) -> dict` (returns new store). Task 2's daemon and Task 6's PUT path call exactly these.

- [ ] **Step 1: Write the failing tests**

```python
# services/garuda-configd/tests/test_whitelist.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from whitelist import validate_request, apply_config_set

def test_rejects_unknown_op():
    ok, why = validate_request({"op": "shell.exec", "args": {"cmd": "rm -rf /"}})
    assert not ok and "not whitelisted" in why

def test_rejects_non_garuda_unit():
    ok, why = validate_request({"op": "unit.action",
                                "args": {"unit": "ssh.service", "action": "stop"}})
    assert not ok

def test_accepts_garuda_unit_restart():
    ok, _ = validate_request({"op": "unit.action",
                              "args": {"unit": "garuda-inference", "action": "restart"}})
    assert ok

def test_rejects_bad_config_key():
    ok, _ = validate_request({"op": "config.set",
                              "args": {"key": "evil.key", "value": 1}})
    assert not ok

def test_accepts_and_applies_bridge_endpoint():
    req = {"op": "config.set", "args": {"key": "bridge.agent_ip", "value": "10.42.0.128"}}
    ok, _ = validate_request(req)
    assert ok
    store = apply_config_set({}, "bridge.agent_ip", "10.42.0.128")
    assert store["bridge.agent_ip"] == "10.42.0.128"

def test_config_set_rejects_bad_value_type():
    ok, _ = validate_request({"op": "config.set",
                              "args": {"key": "bridge.agent_port", "value": "not-an-int"}})
    assert not ok

def test_accepts_system_reboot_only():
    assert validate_request({"op": "system.action", "args": {"action": "reboot"}})[0]
    assert not validate_request({"op": "system.action", "args": {"action": "halt -f"}})[0]
```

- [ ] **Step 2: Run tests, verify they fail** — `pytest services/garuda-configd/tests/ -v` → FAIL (no module `whitelist`).

- [ ] **Step 3: Implement**

```python
# services/garuda-configd/whitelist.py
"""configd whitelist — THE security surface. Everything not listed here is rejected."""
from __future__ import annotations
import ipaddress

def _is_ip(v):
    try: ipaddress.ip_address(v); return True
    except (ValueError, TypeError): return False

CONFIG_SCHEMA = {
    "bridge.agent_ip":   _is_ip,
    "bridge.agent_port": lambda v: isinstance(v, int) and 1024 <= v <= 65535,
    "ptp.enabled":       lambda v: isinstance(v, bool),
    "camera.default_mode": lambda v: isinstance(v, str) and "x" in v and len(v) < 32,
    "inference.hef":     lambda v: isinstance(v, str) and v.endswith(".hef")
                                    and "/" not in v and len(v) < 128,
}
ALLOWED_UNITS = {"garuda-inference", "garuda-cam-portal", "garuda-ptp4l-client"}
ALLOWED_UNIT_ACTIONS = {"start", "stop", "restart"}
ALLOWED_SYSTEM_ACTIONS = {"reboot", "poweroff"}

def validate_request(req: dict) -> tuple[bool, str]:
    if not isinstance(req, dict):
        return False, "request not a dict"
    op, args = req.get("op"), req.get("args", {})
    if op == "config.get":
        return True, "ok"
    if op == "config.set":
        key = args.get("key")
        if key not in CONFIG_SCHEMA:
            return False, f"config key not whitelisted: {key!r}"
        if not CONFIG_SCHEMA[key](args.get("value")):
            return False, f"invalid value for {key!r}"
        return True, "ok"
    if op == "unit.action":
        if args.get("unit") not in ALLOWED_UNITS:
            return False, f"unit not whitelisted: {args.get('unit')!r}"
        if args.get("action") not in ALLOWED_UNIT_ACTIONS:
            return False, f"action not whitelisted: {args.get('action')!r}"
        return True, "ok"
    if op == "system.action":
        if args.get("action") not in ALLOWED_SYSTEM_ACTIONS:
            return False, f"system action not whitelisted: {args.get('action')!r}"
        return True, "ok"
    return False, f"op not whitelisted: {op!r}"

def apply_config_set(store: dict, key: str, value) -> dict:
    new = dict(store)
    new[key] = value
    return new
```

- [ ] **Step 4: Run tests, verify pass** — `pytest services/garuda-configd/tests/ -v` → 7 passed.
- [ ] **Step 5: Commit** — `git add services/garuda-configd && git commit -m "feat(configd): whitelist + config schema with rejection tests"` (+ co-author line).

---

### Task 2: configd daemon (socket loop, audit log, persistence)

**Files:**
- Create: `services/garuda-configd/garuda_configd.py`
- Test: `services/garuda-configd/tests/test_daemon.py`

**Interfaces:**
- Consumes: `validate_request`, `apply_config_set`, `CONFIG_SCHEMA` from Task 1.
- Produces: daemon protocol — newline-delimited JSON request per connection, one JSON reply `{"ok": bool, "detail": str, "result": any}`. `handle_request(req: dict, ctx: Ctx) -> dict` is pure and tested; `Ctx` carries `config_path`, `run_unit(unit, action)`, `run_system(action)` callables (injectable). Socket path default `/run/garuda/configd.sock`.

- [ ] **Step 1: Write failing tests**

```python
# services/garuda-configd/tests/test_daemon.py
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from garuda_configd import Ctx, handle_request

def make_ctx(tmp_path):
    calls = []
    return Ctx(config_path=tmp_path / "config.json",
               run_unit=lambda u, a: calls.append(("unit", u, a)),
               run_system=lambda a: calls.append(("system", a))), calls

def test_rejected_op_is_refused_and_logged(tmp_path, capsys):
    ctx, calls = make_ctx(tmp_path)
    out = handle_request({"op": "shell.exec"}, ctx)
    assert out["ok"] is False and calls == []
    assert "REJECT" in capsys.readouterr().err

def test_config_set_persists(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    out = handle_request({"op": "config.set",
                          "args": {"key": "bridge.agent_port", "value": 8888}}, ctx)
    assert out["ok"]
    assert json.loads(ctx.config_path.read_text())["bridge.agent_port"] == 8888

def test_config_get_roundtrip(tmp_path):
    ctx, _ = make_ctx(tmp_path)
    handle_request({"op": "config.set",
                    "args": {"key": "ptp.enabled", "value": True}}, ctx)
    out = handle_request({"op": "config.get", "args": {}}, ctx)
    assert out["result"]["ptp.enabled"] is True

def test_unit_action_dispatches(tmp_path):
    ctx, calls = make_ctx(tmp_path)
    out = handle_request({"op": "unit.action",
                          "args": {"unit": "garuda-inference", "action": "restart"}}, ctx)
    assert out["ok"] and calls == [("unit", "garuda-inference", "restart")]
```

- [ ] **Step 2: Run, verify FAIL** (`garuda_configd` missing).
- [ ] **Step 3: Implement**

```python
# services/garuda-configd/garuda_configd.py
"""garuda-configd — root-side whitelist executor. Portal is the only client.
Protocol: one JSON request line per connection -> one JSON reply."""
from __future__ import annotations
import dataclasses, json, os, socket, subprocess, sys
from pathlib import Path
from whitelist import validate_request, apply_config_set

SOCK_PATH = "/run/garuda/configd.sock"
CONFIG_PATH = Path("/etc/garuda/config.json")

@dataclasses.dataclass
class Ctx:
    config_path: Path
    run_unit: callable
    run_system: callable

def _load(p: Path) -> dict:
    try: return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError): return {}

def _audit(tag: str, req: dict, detail: str):
    print(f"[configd] {tag} {json.dumps(req, separators=(',', ':'))[:300]} :: {detail}",
          file=sys.stderr, flush=True)

def handle_request(req: dict, ctx: Ctx) -> dict:
    ok, why = validate_request(req)
    if not ok:
        _audit("REJECT", req, why)
        return {"ok": False, "detail": why, "result": None}
    op, args = req["op"], req.get("args", {})
    if op == "config.get":
        _audit("ACCEPT", req, "get")
        return {"ok": True, "detail": "ok", "result": _load(ctx.config_path)}
    if op == "config.set":
        store = apply_config_set(_load(ctx.config_path), args["key"], args["value"])
        ctx.config_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.config_path.write_text(json.dumps(store, indent=2))
        _audit("ACCEPT", req, "set")
        return {"ok": True, "detail": "ok", "result": store}
    if op == "unit.action":
        ctx.run_unit(args["unit"], args["action"])
        _audit("ACCEPT", req, "unit")
        return {"ok": True, "detail": "ok", "result": None}
    if op == "system.action":
        ctx.run_system(args["action"])
        _audit("ACCEPT", req, "system")
        return {"ok": True, "detail": "ok", "result": None}
    return {"ok": False, "detail": "unreachable", "result": None}

def _real_ctx() -> Ctx:
    return Ctx(
        config_path=CONFIG_PATH,
        run_unit=lambda u, a: subprocess.run(["systemctl", a, u], check=False),
        run_system=lambda a: subprocess.run(["systemctl", a], check=False))

def main():
    ctx = _real_ctx()
    os.makedirs(os.path.dirname(SOCK_PATH), exist_ok=True)
    try: os.unlink(SOCK_PATH)
    except FileNotFoundError: pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    os.chmod(SOCK_PATH, 0o660)
    import grp
    try: os.chown(SOCK_PATH, 0, grp.getgrnam("garuda").gr_gid)
    except KeyError: pass
    srv.listen(4)
    print("[configd] listening", file=sys.stderr, flush=True)
    while True:
        conn, _ = srv.accept()
        with conn:
            try:
                data = conn.makefile("r").readline()
                reply = handle_request(json.loads(data), ctx)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                reply = {"ok": False, "detail": f"bad request: {e}", "result": None}
            conn.sendall((json.dumps(reply) + "\n").encode())

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all configd tests, verify pass.**
- [ ] **Step 5: Commit** — `feat(configd): socket daemon with audit log and persistence`.

---

### Task 3: probes module + portal skeleton with /api/health

**Files:**
- Create: `services/garuda-portal/probes.py`, `services/garuda-portal/portal.py`, `services/garuda-portal/configd_client.py`
- Test: `services/garuda-portal/tests/test_probes.py`

**Interfaces:**
- Produces: `probes.collect_health(run=shell_runner) -> dict` with keys `throttled` (str), `temp_c` (float|None), `npu` (str|None), `services` (dict unit->bool), `disk_free_gb` (float), `uptime_s` (float). `shell_runner(cmd: list[str]) -> str` is the injection point. `configd_client.call(op, args, sock_path) -> dict`. `portal.build_server(port, deps) -> ThreadingHTTPServer` where `deps` bundles probes/stream/etc. Task 4+ extend `deps`.

- [ ] **Step 1: Failing tests**

```python
# services/garuda-portal/tests/test_probes.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import probes

FAKE = {
    ("vcgencmd", "get_throttled"): "throttled=0x0\n",
    ("vcgencmd", "measure_temp"): "temp=48.2'C\n",
    ("hailortcli", "fw-control", "identify"): "Device Architecture: HAILO10H\n",
    ("systemctl", "is-active", "garuda-inference"): "inactive\n",
    ("systemctl", "is-active", "garuda-cam-portal"): "active\n",
}
def fake_run(cmd):
    return FAKE.get(tuple(cmd), "")

def test_collect_health_parses_probes():
    h = probes.collect_health(run=fake_run)
    assert h["throttled"] == "0x0"
    assert h["temp_c"] == 48.2
    assert h["npu"] == "HAILO10H"
    assert h["services"]["garuda-cam-portal"] is True
    assert h["services"]["garuda-inference"] is False

def test_collect_health_survives_missing_tools():
    h = probes.collect_health(run=lambda cmd: (_ for _ in ()).throw(FileNotFoundError()))
    assert h["throttled"] == "unknown" and h["npu"] is None
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement**

```python
# services/garuda-portal/probes.py
"""Health probes. All shell access goes through the injected `run` callable."""
from __future__ import annotations
import re, shutil, subprocess, time

WATCHED_UNITS = ["garuda-inference", "garuda-cam-portal"]

def shell_runner(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout

def _safe(run, cmd) -> str:
    try: return run(list(cmd))
    except Exception: return ""

def collect_health(run=shell_runner) -> dict:
    throttled = "unknown"
    m = re.search(r"throttled=(0x[0-9a-fA-F]+)", _safe(run, ["vcgencmd", "get_throttled"]))
    if m: throttled = m.group(1)
    temp = None
    m = re.search(r"temp=([\d.]+)", _safe(run, ["vcgencmd", "measure_temp"]))
    if m: temp = float(m.group(1))
    npu = None
    m = re.search(r"Device Architecture:\s*(\S+)",
                  _safe(run, ["hailortcli", "fw-control", "identify"]))
    if m: npu = m.group(1)
    services = {u: _safe(run, ["systemctl", "is-active", u]).strip() == "active"
                for u in WATCHED_UNITS}
    du = shutil.disk_usage("/")
    with open("/proc/uptime") as f:
        uptime = float(f.read().split()[0])
    return {"throttled": throttled, "temp_c": temp, "npu": npu, "services": services,
            "disk_free_gb": round(du.free / 1e9, 1), "uptime_s": uptime,
            "t_probe_ns": time.monotonic_ns()}
```

```python
# services/garuda-portal/configd_client.py
import json, socket

def call(op: str, args: dict, sock_path: str = "/run/garuda/configd.sock") -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    try:
        s.connect(sock_path)
        s.sendall((json.dumps({"op": op, "args": args}) + "\n").encode())
        return json.loads(s.makefile("r").readline())
    finally:
        s.close()
```

```python
# services/garuda-portal/portal.py
"""garuda-portal HTTP wiring. Routes only — logic lives in the other modules."""
from __future__ import annotations
import json, pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATIC = pathlib.Path(__file__).parent / "static"

def build_server(port: int, deps: dict) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/health":
                return self._json(deps["health"]())
            if self.path.startswith("/api/"):
                extra = deps.get("get_routes", {}).get(self.path.split("?")[0])
                if extra: return extra(self)
                return self._json({"error": "not found"}, 404)
            name = "index.html" if self.path == "/" else self.path.lstrip("/")
            f = (STATIC / name)
            if f.is_file() and STATIC in f.resolve().parents:
                ctype = {"html": "text/html", "js": "text/javascript",
                         "css": "text/css"}.get(f.suffix[1:], "application/octet-stream")
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            fn = deps.get("post_routes", {}).get(self.path)
            if not fn: return self._json({"error": "not found"}, 404)
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            return fn(self, body)

        def do_PUT(self): self.do_POST()
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)

def main():
    import probes
    server = build_server(8080, {"health": probes.collect_health})
    server.serve_forever()

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests, verify pass. Also smoke locally:** `cd services/garuda-portal && python3 -c "import portal, probes; s=portal.build_server(8099, {'health': lambda: {'ok':1}}); import threading; threading.Thread(target=s.serve_forever, daemon=True).start(); import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8099/api/health').read())"` → `{"ok": 1}`.
- [ ] **Step 5: Commit** — `feat(portal): probes + HTTP skeleton with /api/health`.

---

### Task 4: camera MJPEG stream + SSE event bus

**Files:**
- Create: `services/garuda-portal/camera_stream.py`, `services/garuda-portal/events.py`
- Test: `services/garuda-portal/tests/test_events.py`

**Interfaces:**
- Consumes: `build_server` deps dict (Task 3).
- Produces: `events.EventBus` with `.publish(kind: str, data: dict)` and `.subscribe() -> queue.Queue`; `camera_stream.CameraStream(cmd: list[str])` with `.latest` (bytes|None), `.start()`, `.stop()`, `.stalled(max_age_s) -> bool` — same frame parser + watchdog logic as bench `cam_portal.py` (that file's lessons: parse SOI/EOI, watchdog on frame age). GET routes `/api/stream` (MJPEG multipart) and `/api/events` (SSE `data: {json}\n\n`).

- [ ] **Step 1: Failing tests**

```python
# services/garuda-portal/tests/test_events.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from events import EventBus
from camera_stream import split_mjpeg

def test_bus_fanout():
    bus = EventBus()
    q1, q2 = bus.subscribe(), bus.subscribe()
    bus.publish("health", {"x": 1})
    assert q1.get_nowait()["kind"] == "health"
    assert q2.get_nowait()["data"] == {"x": 1}

def test_split_mjpeg_extracts_frames():
    junk = b"xx"
    f1 = b"\xff\xd8\xff" + b"A" * 5 + b"\xff\xd9"
    f2 = b"\xff\xd8\xff" + b"B" * 3 + b"\xff\xd9"
    frames, rest = split_mjpeg(junk + f1 + f2 + b"\xff\xd8\xffpartial")
    assert frames == [f1, f2]
    assert rest.startswith(b"\xff\xd8\xff")
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement**

```python
# services/garuda-portal/events.py
import json, queue, threading, time

class EventBus:
    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=100)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs: self._subs.remove(q)

    def publish(self, kind: str, data: dict):
        ev = {"kind": kind, "data": data, "t_ns": time.monotonic_ns()}
        with self._lock:
            for q in list(self._subs):
                try: q.put_nowait(ev)
                except queue.Full: pass
```

```python
# services/garuda-portal/camera_stream.py
"""MJPEG child-process reader with the cam_portal wedge lessons baked in."""
from __future__ import annotations
import subprocess, threading, time

def split_mjpeg(buf: bytes):
    frames = []
    while True:
        s = buf.find(b"\xff\xd8\xff")
        e = buf.find(b"\xff\xd9", s + 3) if s >= 0 else -1
        if s < 0 or e < 0:
            return frames, (buf[s:] if s > 0 else buf)
        frames.append(buf[s:e + 2])
        buf = buf[e + 2:]

DEFAULT_CMD = ["rpicam-vid", "--nopreview", "-t", "0", "--codec", "mjpeg",
               "--width", "1280", "--height", "720", "--framerate", "15", "-o", "-"]

class CameraStream:
    def __init__(self, cmd=None):
        self.cmd = cmd or DEFAULT_CMD
        self.latest: bytes | None = None
        self.t_last = 0.0
        self.cond = threading.Condition()
        self._proc = None
        self._run = False

    def start(self):
        self._run = True
        threading.Thread(target=self._reader, daemon=True).start()

    def stop(self):
        self._run = False
        if self._proc: self._proc.kill()

    def stalled(self, max_age_s=15.0) -> bool:
        return self.t_last > 0 and (time.monotonic() - self.t_last) > max_age_s

    def _reader(self):
        while self._run:
            self._proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL)
            buf = b""
            while self._run:
                chunk = self._proc.stdout.read(65536)
                if not chunk: break
                frames, buf = split_mjpeg(buf + chunk)
                if frames:
                    with self.cond:
                        self.latest = frames[-1]
                        self.t_last = time.monotonic()
                        self.cond.notify_all()
            self._proc.kill()
            time.sleep(2)   # relaunch loop = the Restart=always lesson, in-process
```

Wire into `portal.py` deps (`get_routes`): `/api/stream` loops writing `--frame` multipart parts from `stream.cond`/`stream.latest` (copy the working loop from bench `cam_portal.py` Handler.do_GET, adapted to `deps["stream"]`); `/api/events` writes SSE: subscribe to bus, `data: {json.dumps(ev)}\n\n` per event, unsubscribe on client disconnect. A background thread publishes `health` events every 2 s via `probes.collect_health`.

- [ ] **Step 4: Run tests → pass. Local smoke without camera:** feed `split_mjpeg` via unit test only (camera integration is Task 9 on-bench).
- [ ] **Step 5: Commit** — `feat(portal): event bus + camera stream module with stall detection`.

---

### Task 5: static UI — overview, camera, inference panels

**Files:**
- Create: `services/garuda-portal/static/index.html`, `static/app.js`, `static/style.css`

**Interfaces:**
- Consumes: `/api/health`, `/api/events` (SSE), `/api/stream`, `POST /api/actions/service` `{unit, action}` (added in this task to portal deps via configd client), `GET /api/models` (scans `/usr/share/hailo-models/*_h10.hef`).
- Produces: nav-panel SPA the later tasks add panels into (`<section id="panel-<name>">` + `nav` entries; `app.js` `showPanel(name)`).

- [ ] **Step 1: index.html** — dark theme consistent with bench portal page; nav: Overview · Camera · Inference · Calibration · Readiness · Settings · System; sections per panel; Overview shows throttled/temp/NPU/services/disk tiles updated from SSE; Camera embeds `<img src="/api/stream">` + still-capture button; Inference lists models (select) + start/stop buttons + live detections `<ul>` fed from SSE `detections` events.
- [ ] **Step 2: app.js** — `EventSource('/api/events')` dispatch by `kind` → tile updates; `fetch` wrappers for actions; `showPanel` router. Keep it < 200 lines, no framework.
- [ ] **Step 3: portal deps additions** — `get_routes["/api/models"]` (list of `*_h10.hef` basenames); `get_routes["/api/logs"]` (last 200 journal lines via the probes runner, `text/plain`); `post_routes["/api/actions/service"]` → `configd_client.call("unit.action", …)`; `post_routes["/api/actions/capture-still"]` → save from `stream.latest` to `/home/garuda/portal_stills/<ts>.jpg`, return filename.

  **Camera ownership rule (single-client hardware):** the MJPEG stream child and the
  garuda-inference pipeline cannot both hold the camera. The portal owns ONE camera child
  at a time: `deps["camera_mode"] ∈ {"stream", "inference"}`. Inference panel Start:
  stop `CameraStream`, spawn `python3 /opt/garuda/garuda-inference/garuda_inference.py`
  as a portal child, parse its stdout JSONL, publish each DetectionSet as a `detections`
  event on the bus (this is how the Inference panel list gets data). Stop reverses it.
  Camera panel shows "camera in use by inference" while in inference mode. The
  `unit.action` route remains for future units but V1 inference start/stop is
  child-process-based for exactly this reason.
- [ ] **Step 4: Manual smoke on the dev box:** `python3 portal.py` with a fake health lambda + no camera → page loads, tiles render "unknown", SSE ticks visible in devtools.
- [ ] **Step 5: Commit** — `feat(portal): SPA skeleton with overview/camera/inference panels`.

---

### Task 6: settings panel + config path end-to-end

**Files:**
- Modify: `services/garuda-portal/portal.py` (routes), `static/app.js`, `static/index.html`
- Test: `services/garuda-portal/tests/test_config_route.py`

**Interfaces:**
- Consumes: `configd_client.call` (Task 3), whitelist semantics (Task 1).
- Produces: `GET /api/config` → `{"ok": true, "result": {…}}`; `PUT /api/config` body `{"key": …, "value": …}` → configd reply passed through verbatim (including rejections — the UI must show *why* a set was refused).

- [ ] **Step 1: Failing test** — with a stub configd server on a temp socket:

```python
# services/garuda-portal/tests/test_config_route.py
import json, socket, sys, threading, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import configd_client

def stub_configd(sock_path, reply):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path)); srv.listen(1)
    def run():
        conn, _ = srv.accept()
        conn.makefile("r").readline()
        conn.sendall((json.dumps(reply) + "\n").encode())
        conn.close()
    threading.Thread(target=run, daemon=True).start()
    return srv

def test_client_roundtrip(tmp_path):
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": True, "detail": "ok", "result": {"ptp.enabled": True}})
    out = configd_client.call("config.get", {}, sock_path=str(sp))
    assert out["ok"] and out["result"]["ptp.enabled"] is True

def test_client_conveys_rejection(tmp_path):
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": False, "detail": "config key not whitelisted: 'x'",
                      "result": None})
    out = configd_client.call("config.set", {"key": "x", "value": 1}, sock_path=str(sp))
    assert out["ok"] is False and "not whitelisted" in out["detail"]
```

- [ ] **Step 2: FAIL → Step 3: wire routes** (`get_routes["/api/config"]`, PUT handler in `post_routes["/api/config"]` calling `config.set`), Settings panel form (bridge IP/port, PTP toggle, GARUDA_SIM display sourced from health), rejection detail surfaced in a toast div.
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit** — `feat(portal): settings panel wired through configd whitelist`.

---

### Task 7: readiness engine + panel

**Files:**
- Create: `services/garuda-portal/readiness.py`
- Modify: `portal.py`, `static/*` (Readiness panel)
- Test: `services/garuda-portal/tests/test_readiness.py`

**Interfaces:**
- Consumes: `probes.collect_health` output shape (Task 3).
- Produces: `readiness.run_checks(health: dict, config: dict) -> dict` report: `{"t_ns": int, "overall": "go"|"no-go"|"caution", "checks": [{"id","status" ∈ pass|warn|fail|na,"evidence"}]}`. Route `GET /api/readiness/report` (runs fresh), UI card.

- [ ] **Step 1: Failing tests**

```python
# services/garuda-portal/tests/test_readiness.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from readiness import run_checks

HEALTHY = {"throttled": "0x0", "temp_c": 50.0, "npu": "HAILO10H",
           "services": {"garuda-inference": True, "garuda-cam-portal": True},
           "disk_free_gb": 100.0, "uptime_s": 600.0}

def test_all_green_is_go():
    r = run_checks(HEALTHY, {"bridge.agent_ip": None})
    assert r["overall"] == "go"
    assert all(c["status"] in ("pass", "warn", "na") for c in r["checks"])

def test_uv_flags_block():
    r = run_checks({**HEALTHY, "throttled": "0x50000"}, {})
    power = next(c for c in r["checks"] if c["id"] == "power.flags")
    assert power["status"] == "fail" and r["overall"] == "no-go"

def test_missing_npu_blocks():
    r = run_checks({**HEALTHY, "npu": None}, {})
    assert r["overall"] == "no-go"

def test_hot_soc_warns_not_blocks():
    r = run_checks({**HEALTHY, "temp_c": 81.0}, {})
    therm = next(c for c in r["checks"] if c["id"] == "thermals")
    assert therm["status"] == "warn" and r["overall"] == "caution"

def test_bridge_na_when_unconfigured():
    r = run_checks(HEALTHY, {})
    b = next(c for c in r["checks"] if c["id"] == "bridge.session")
    assert b["status"] == "na"
```

- [ ] **Step 2: FAIL → Step 3: implement**

```python
# services/garuda-portal/readiness.py
"""Declarative readiness checks. Pure function of (health, config) — no shell here."""
from __future__ import annotations
import time

def run_checks(health: dict, config: dict) -> dict:
    checks = []
    def add(id, status, evidence):
        checks.append({"id": id, "status": status, "evidence": evidence})

    add("npu.identify", "pass" if health.get("npu") == "HAILO10H" else "fail",
        f"identify: {health.get('npu')}")
    flags = health.get("throttled", "unknown")
    add("power.flags", "pass" if flags == "0x0" else "fail", f"get_throttled={flags}")
    add("power.supply5a", "warn",
        "supply not verified 5V/5A (bench charger has prior UV strikes)")
    t = health.get("temp_c")
    add("thermals", "na" if t is None else ("pass" if t < 75 else "warn"),
        f"SoC {t} C")
    svc = health.get("services", {})
    add("services.core", "pass" if all(svc.values()) and svc else "warn",
        ", ".join(f"{k}={'up' if v else 'down'}" for k, v in svc.items()) or "none")
    add("storage.space", "pass" if health.get("disk_free_gb", 0) > 5 else "warn",
        f"{health.get('disk_free_gb')} GB free")
    if config.get("bridge.agent_ip"):
        add("bridge.session", "warn",
            "endpoint configured; live session not probed in V1 (needs FC/SITL up)")
    else:
        add("bridge.session", "na", "no FC endpoint configured")
    add("simgate.status", "pass", "portal sends no arming commands (by design)")

    # Overall rule: any fail -> no-go; any warn EXCEPT power.supply5a -> caution;
    # else go. supply5a is a permanent advisory on this bench (unverified charger)
    # and must not demote an otherwise-green board.
    statuses = [c["status"] for c in checks]
    non_supply_warns = [c for c in checks
                        if c["status"] == "warn" and c["id"] != "power.supply5a"]
    overall = ("no-go" if "fail" in statuses
               else "caution" if non_supply_warns else "go")
    return {"t_ns": time.monotonic_ns(), "overall": overall, "checks": checks}
```

- [ ] **Step 4: Tests pass** (adjust `test_all_green_is_go` expects `go` despite supply warn — that is the intended behavior).
- [ ] **Step 5: Wire route + UI card** (green/amber/red per check, big GO/NO-GO banner, re-run button, download link `readiness-<date>.json`).
- [ ] **Step 6: Commit** — `feat(portal): readiness engine + go/no-go panel`.

---

### Task 8: calibration backend + wizard panel

**Files:**
- Create: `services/garuda-portal/calibration.py`
- Modify: `portal.py`, `static/*` (Calibration panel)
- Test: `services/garuda-portal/tests/test_calibration.py`

**Interfaces:**
- Consumes: `camera_stream.CameraStream.latest` (Task 4) for capture frames.
- Produces: `calibration.CoverageGrid(rows=3, cols=3, per_zone=3)` with `.add(cx, cy) -> bool` (normalized corner centroid; returns True if it filled a needed zone) and `.complete` property; `calibration.solve(object_points, image_points, image_size) -> {"rms": float, "K": [[...]], "dist": [...]}`; routes `POST /api/actions/calib-capture`, `POST /api/actions/calib-solve`, `GET /api/calibration/status`. Calib store: `/etc/garuda/calib/<sensor>.json` written via configd? No — calib files are portal-owned data, written by portal to `/home/garuda/garuda-calib/<sensor>.json` in V1 (root store migration when garuda-camera exists). Keep V1 honest and unprivileged.

- [ ] **Step 1: Failing tests**

```python
# services/garuda-portal/tests/test_calibration.py
import sys, pathlib
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
cv2 = pytest.importorskip("cv2")
from calibration import CoverageGrid, solve, find_corners

def test_coverage_grid_fills_and_completes():
    g = CoverageGrid(rows=3, cols=3, per_zone=1)
    assert not g.complete
    for r in range(3):
        for c in range(3):
            assert g.add((c + 0.5) / 3, (r + 0.5) / 3)
    assert g.complete
    assert g.add(0.5, 0.5) is False  # zone already full

def test_solve_recovers_known_camera():
    # synthetic 9x6 board seen from 12 poses through a known camera
    K = np.array([[600.0, 0, 320], [0, 600.0, 240], [0, 0, 1]])
    obj = np.zeros((9 * 6, 3), np.float32)
    obj[:, :2] = np.mgrid[0:9, 0:6].T.reshape(-1, 2) * 0.025
    objpoints, imgpoints = [], []
    rng = np.random.default_rng(7)
    for i in range(12):
        rvec = rng.normal(0, 0.2, 3); tvec = np.array([0.05, 0.05, 0.5 + 0.05 * i])
        img, _ = cv2.projectPoints(obj, rvec, tvec, K, None)
        objpoints.append(obj); imgpoints.append(img.astype(np.float32))
    out = solve(objpoints, imgpoints, (640, 480))
    assert out["rms"] < 0.1
    assert abs(out["K"][0][0] - 600.0) < 5.0

def test_find_corners_on_synthetic_board():
    board = np.full((480, 640), 255, np.uint8)
    sq = 40
    for r in range(7):
        for c in range(10):
            if (r + c) % 2 == 0:
                board[40 + r * sq:40 + (r + 1) * sq, 60 + c * sq:60 + (c + 1) * sq] = 0
    found, centroid = find_corners(board, (9, 6))
    assert found and 0.2 < centroid[0] < 0.8
```

- [ ] **Step 2: FAIL → Step 3: implement**

```python
# services/garuda-portal/calibration.py
"""Guided mono intrinsics: coverage grid logic (pure) + OpenCV corner/solve."""
from __future__ import annotations
import cv2
import numpy as np

class CoverageGrid:
    def __init__(self, rows=3, cols=3, per_zone=3):
        self.rows, self.cols, self.per_zone = rows, cols, per_zone
        self.counts = [[0] * cols for _ in range(rows)]

    def add(self, cx: float, cy: float) -> bool:
        c = min(int(cx * self.cols), self.cols - 1)
        r = min(int(cy * self.rows), self.rows - 1)
        if self.counts[r][c] >= self.per_zone:
            return False
        self.counts[r][c] += 1
        return True

    @property
    def complete(self) -> bool:
        return all(n >= self.per_zone for row in self.counts for n in row)

def find_corners(gray, pattern=(9, 6)):
    found, corners = cv2.findChessboardCorners(gray, pattern,
        flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not found:
        return False, (0.0, 0.0)
    c = corners.reshape(-1, 2).mean(axis=0)
    h, w = gray.shape[:2]
    return True, (float(c[0] / w), float(c[1] / h))

def solve(objpoints, imgpoints, image_size) -> dict:
    rms, K, dist, _, _ = cv2.calibrateCamera(objpoints, imgpoints, image_size,
                                             None, None)
    return {"rms": float(rms), "K": K.tolist(), "dist": dist.ravel().tolist()}
```

- [ ] **Step 4: Tests pass** (needs `pip install opencv-python-headless` in dev venv OR `python3-opencv` — on the PC use a scratch venv; on the Pi apt).
- [ ] **Step 5: Wire wizard state into portal** — module-level `CalibSession` in `portal.py` deps: holds pattern dims, square size, `CoverageGrid`, captured obj/img points (decode `stream.latest` JPEG → gray via cv2.imdecode → `find_corners` → `grid.add` → keep full corner arrays). `calib-capture` returns `{added, zone_counts, complete, corners_found}`. `calib-solve` returns solve dict + writes `/home/garuda/garuda-calib/imx708.cam0.json` with `{K, dist, rms, pattern, t_saved}` and history append. Panel UI: pattern form → live grid heatmap (3×3 divs colored by counts) → capture button (V1 manual trigger; auto-capture deferred, honest) → solve → verdict badge (<0.5 px green / <1.0 amber / else red).
- [ ] **Step 6: Commit** — `feat(portal): guided intrinsics calibration (grid, corners, solve)`.

---

### Task 9: systemd units, install.sh, bench deploy + end-to-end

**Files:**
- Create: `hosts/pi5/units/garuda-portal.service`, `hosts/pi5/units/garuda-configd.service`
- Modify: `hosts/pi5/install.sh`

**Interfaces:**
- Consumes: everything above.
- Produces: running bench deployment; benchlog entry.

- [ ] **Step 1: Unit files**

```ini
# hosts/pi5/units/garuda-configd.service
[Unit]
Description=Garuda configd (whitelisted root executor)
[Service]
ExecStart=/usr/bin/python3 /opt/garuda/garuda-configd/garuda_configd.py
Restart=always
RestartSec=3
RuntimeDirectory=garuda
[Install]
WantedBy=multi-user.target
```

```ini
# hosts/pi5/units/garuda-portal.service
[Unit]
Description=Garuda portal (web configurator)
After=garuda-configd.service
[Service]
User=garuda
ExecStart=/usr/bin/python3 /opt/garuda/garuda-portal/portal.py
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: install.sh additions** (idempotent, check-then-modify per CLAUDE.md §6.3): add `python3-opencv` to the apt install list; new step `[5/5] portal deploy`: `rsync` `services/garuda-{portal,configd}` + `interfaces/garuda_interfaces` → `/opt/garuda/`, `install -m644` unit files → `/etc/systemd/system/`, `systemctl daemon-reload`, `enable --now` both units. **Port conflict:** portal replaces bench `garuda-cam-portal` (same camera + same :8080) — install.sh stops/disables `garuda-cam-portal` first, and `WATCHED_UNITS`/whitelist keep working because the portal serves the stream itself now.
- [ ] **Step 3: Deploy to bench** — run install.sh on the Pi (over ssh), open `http://10.42.0.128:8080` from the PC **and a phone**, walk the manual checklist: overview tiles live, stream plays, model list correct, service start/stop works, settings set `bridge.agent_port=8888` persists + non-whitelisted key refused with reason shown, readiness card renders GO with supply warning amber.
- [ ] **Step 4: Record in benchlog** — `bench/benchlog.md` entry: portal V1 deployed; readiness report output pasted (statuses only); any deviations noted.
- [ ] **Step 5: Commit** — `feat(portal): systemd units + idempotent deploy; bench E2E recorded`.

---

### Task 10: red-team pass on the security surface

**Files:**
- Modify: `services/garuda-configd/tests/test_whitelist.py` (additions)

- [ ] **Step 1: Adversarial tests** — add and make pass:

```python
def test_rejects_unit_with_path_traversal():
    ok, _ = validate_request({"op": "unit.action",
        "args": {"unit": "garuda-inference ../../ssh", "action": "restart"}})
    assert not ok

def test_rejects_hef_with_path_traversal():
    ok, _ = validate_request({"op": "config.set",
        "args": {"key": "inference.hef", "value": "../../etc/passwd.hef"}})
    assert not ok

def test_rejects_oversized_request():
    ok, _ = validate_request({"op": "config.set",
        "args": {"key": "camera.default_mode", "value": "x" * 10000}})
    assert not ok

def test_get_never_requires_args():
    assert validate_request({"op": "config.get"})[0]
```

- [ ] **Step 2: Fix any that fail** (the `"/" not in v` hef rule and length caps should already hold; if a test exposes a hole, tighten `whitelist.py`, never the test).
- [ ] **Step 3: Full suite green** — `pytest services/ -v`.
- [ ] **Step 4: Commit** — `test(configd): adversarial whitelist coverage`.
