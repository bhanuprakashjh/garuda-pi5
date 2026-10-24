import sys, pathlib, json, threading
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from inference_manager import InferenceManager

class FakeStream:
    def __init__(self):
        self.start_calls = 0
        self.stop_calls = 0
    def start(self):
        self.start_calls += 1
    def stop(self):
        self.stop_calls += 1

class FakeBus:
    def __init__(self):
        self.events = []
    def publish(self, kind, data):
        self.events.append((kind, data))

class FakeReadable:
    """stdout stand-in: read(n) returns queued byte chunks then b'' (EOF)."""
    def __init__(self, chunks):
        self._chunks = list(chunks)
    def read(self, n=-1):
        return self._chunks.pop(0) if self._chunks else b""

class FakeProc:
    """Finite child: stdout yields byte chunks then EOF; stderr yields binary
    JSONL lines then ends. Both exhausting == the child exited on its own — use
    this whenever a test wants the reader threads to actually finish."""
    def __init__(self, stdout_chunks=(), stderr_lines=()):
        self.stdout = FakeReadable(stdout_chunks)
        self.stderr = iter(stderr_lines)
        self.killed = False
    def kill(self):
        self.killed = True

class LiveFakeProc:
    """Still-running child: stdout.read() and stderr iteration both block until
    kill(), so it can never race a test's own start()/stop() via a spurious EOF."""
    def __init__(self):
        self.killed = False
        self._stop = threading.Event()
        self.stdout = self
        self.stderr = self
    def read(self, n=-1):
        self._stop.wait()
        return b""
    def __iter__(self):
        return self
    def __next__(self):
        self._stop.wait()
        raise StopIteration
    def kill(self):
        self.killed = True
        self._stop.set()

def det_line(n):
    return json.dumps({"topic": "detections", "msg": {"n": n}}).encode() + b"\n"

def jpeg(marker):
    return b"\xff\xd8\xff" + marker + b"\xff\xd9"

def join_readers(mgr, timeout=1.0):
    for t in (mgr._frame_thread, mgr._det_thread):
        if t:
            t.join(timeout=timeout)

def test_start_stops_camera_spawns_child_publishes_detections_and_frames():
    stream, bus = FakeStream(), FakeBus()
    stderr = [det_line(1), det_line(2), b"not json\n", b""]
    stdout = [jpeg(b"AAA"), jpeg(b"BBB")]
    spawned = {}
    def spawn(cmd, **kwargs):
        spawned["cmd"] = cmd
        spawned["kwargs"] = kwargs
        proc = FakeProc(stdout_chunks=stdout, stderr_lines=stderr)
        spawned["proc"] = proc
        return proc
    mgr = InferenceManager(stream, bus, cmd=["fake-inference"], spawn=spawn)

    result = mgr.start()

    assert result == {"ok": True, "mode": "inference"}
    assert stream.stop_calls == 1
    assert spawned["cmd"] == ["fake-inference"]
    # binary pipes, no text= (would corrupt the JPEG byte stream)
    assert spawned["kwargs"].get("text") in (None, False)

    join_readers(mgr)
    assert not mgr._frame_thread.is_alive() and not mgr._det_thread.is_alive()

    det_events = [data for kind, data in bus.events if kind == "detections"]
    assert det_events == [{"n": 1}, {"n": 2}]  # from stderr; bad line skipped
    assert mgr.preview.latest == jpeg(b"BBB")   # last annotated frame from stdout
    assert any(kind == "camera_mode" and data == {"mode": "inference"} for kind, data in bus.events)
    # both pipes hit EOF => child-exit recovery (idempotent across the two threads)
    assert mgr.mode == "stream"
    assert stream.start_calls == 1

def test_active_source_switches_by_mode():
    stream, bus = FakeStream(), FakeBus()
    proc = LiveFakeProc()
    mgr = InferenceManager(stream, bus, spawn=lambda cmd, **kw: proc)

    assert mgr.active_source() is stream          # stream mode -> raw camera
    mgr.start()
    assert mgr.active_source() is mgr.preview      # inference mode -> annotated preview
    mgr.stop()
    proc.kill()
    assert mgr.active_source() is stream

def test_start_is_idempotent_when_already_inference():
    stream, bus = FakeStream(), FakeBus()
    spawn_calls, procs = [], []
    def spawn(cmd, **kwargs):
        spawn_calls.append(cmd)
        proc = LiveFakeProc()
        procs.append(proc)
        return proc
    mgr = InferenceManager(stream, bus, spawn=spawn)

    mgr.start()
    result = mgr.start()

    assert result == {"ok": True, "mode": "inference"}
    assert len(spawn_calls) == 1

    mgr.stop()
    for p in procs:
        p.kill()

def test_start_spawn_failure_reverts_to_stream_and_emits_error():
    stream, bus = FakeStream(), FakeBus()
    def spawn(cmd, **kwargs):
        raise OSError("no such file or directory")
    mgr = InferenceManager(stream, bus, spawn=spawn)

    result = mgr.start()

    assert result["ok"] is False
    assert "no such file" in result["error"]
    assert mgr.mode == "stream"
    assert stream.stop_calls == 1
    assert stream.start_calls == 1
    error_events = [data for kind, data in bus.events if kind == "error"]
    assert len(error_events) == 1
    assert error_events[0]["stage"] == "inference-start"

def test_stop_kills_child_and_restarts_camera():
    stream, bus = FakeStream(), FakeBus()
    proc = LiveFakeProc()
    mgr = InferenceManager(stream, bus, spawn=lambda cmd, **kw: proc)
    mgr.start()

    result = mgr.stop()

    assert result == {"ok": True, "mode": "stream"}
    assert proc.killed
    assert stream.start_calls == 1
    assert any(kind == "camera_mode" and data == {"mode": "stream"} for kind, data in bus.events)

def test_stop_is_noop_when_already_stream():
    stream, bus = FakeStream(), FakeBus()
    mgr = InferenceManager(stream, bus)

    result = mgr.stop()

    assert result == {"ok": True, "mode": "stream"}
    assert stream.start_calls == 0
    assert stream.stop_calls == 0
    assert bus.events == []

def test_get_mode_reflects_start_and_stop_transitions():
    stream, bus = FakeStream(), FakeBus()
    proc = LiveFakeProc()
    mgr = InferenceManager(stream, bus, spawn=lambda cmd, **kw: proc)

    assert mgr.get_mode() == "stream"
    mgr.start()
    assert mgr.get_mode() == "inference"
    mgr.stop()
    assert mgr.get_mode() == "stream"

def test_deps_health_includes_camera_mode_before_and_after_start():
    import portal
    stream, bus = FakeStream(), FakeBus()
    proc = LiveFakeProc()
    mgr = InferenceManager(stream, bus, spawn=lambda cmd, **kw: proc)
    fake_collect_health = lambda: {"throttled": "0x0"}
    health = lambda: portal.health_with_camera_mode(fake_collect_health, mgr.get_mode)

    before = health()
    assert before["camera_mode"] == "stream"
    assert before["throttled"] == "0x0"

    mgr.start()
    after = health()
    assert after["camera_mode"] == "inference"

    mgr.stop()
    proc.kill()

def test_child_exit_without_explicit_stop_triggers_recovery():
    stream, bus = FakeStream(), FakeBus()
    # one detection then EOF on both pipes (child exited)
    mgr = InferenceManager(stream, bus,
                           spawn=lambda cmd, **kw: FakeProc(stderr_lines=[det_line(1)]))

    mgr.start()
    join_readers(mgr)

    assert not mgr._frame_thread.is_alive() and not mgr._det_thread.is_alive()
    assert mgr.mode == "stream"
    assert stream.start_calls == 1
    error_events = [data for kind, data in bus.events if kind == "error"]
    assert any(e.get("detail") == "inference child exited" for e in error_events)
    assert any(kind == "camera_mode" and data == {"mode": "stream"} for kind, data in bus.events)

def test_child_exit_recovery_is_noop_if_already_stopped():
    """An explicit stop() winning the race must make a reader thread's own (late)
    recovery attempt for the OLD proc a pure no-op: no duplicate kill/events."""
    stream, bus = FakeStream(), FakeBus()
    old_proc = LiveFakeProc()
    mgr = InferenceManager(stream, bus, spawn=lambda cmd, **kw: old_proc)

    mgr.start()
    mgr.stop()
    bus.events.clear()
    stream.start_calls = 0

    mgr._on_child_exit(old_proc)  # simulate a reader thread's finally firing late

    assert mgr.mode == "stream"
    assert bus.events == []
    assert stream.start_calls == 0
