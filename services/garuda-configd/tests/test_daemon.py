import json, sys, pathlib, socket
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from garuda_configd import Ctx, handle_request, serve_connection, _load

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

# Review Finding 1: serve_connection exception safety
def test_serve_connection_broken_pipe_no_crash(tmp_path, capsys):
    """BrokenPipeError on sendall must not crash daemon; REJECT logged."""
    ctx, _ = make_ctx(tmp_path)
    class FakeConnBrokenPipe:
        def settimeout(self, t): pass
        def makefile(self, mode):
            class FakeFile:
                def readline(self): return '{"op":"config.get","args":{}}'
            return FakeFile()
        def sendall(self, data):
            raise BrokenPipeError("client disconnected")
        def __enter__(self): return self
        def __exit__(self, *args): pass

    serve_connection(FakeConnBrokenPipe(), ctx)
    captured = capsys.readouterr().err
    assert "REJECT" in captured and "sendall failed" in captured

# Review Finding 3: malformed JSON audit logging
def test_malformed_json_rejected_and_logged(tmp_path, capsys):
    """Non-JSON line must return ok:False AND log REJECT."""
    ctx, _ = make_ctx(tmp_path)
    class FakeConnBadJson:
        def settimeout(self, t): pass
        def makefile(self, mode):
            class FakeFile:
                def readline(self): return 'not valid json\n'
            return FakeFile()
        def sendall(self, data):
            assert json.loads(data.decode())["ok"] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass

    serve_connection(FakeConnBadJson(), ctx)
    captured = capsys.readouterr().err
    assert "REJECT" in captured and "malformed" in captured

# Review Finding 4: corrupt config file warning
def test_corrupt_config_file_warns_and_returns_empty(tmp_path, capsys):
    """Corrupt JSON config must warn to stderr and return {}."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{ broken json")
    loaded = _load(cfg_path)
    assert loaded == {}
    captured = capsys.readouterr().err
    assert "WARN corrupt config" in captured

# Review Finding 1: internal exception handling
def test_ctx_run_unit_exception_returns_error(tmp_path):
    """ctx.run_unit raising exception must return ok:False internal error."""
    ctx = Ctx(config_path=tmp_path / "config.json",
              run_unit=lambda u, a: (_ for _ in ()).throw(RuntimeError("test error")),
              run_system=lambda a: None)
    out = handle_request({"op": "unit.action",
                          "args": {"unit": "garuda-inference", "action": "restart"}}, ctx)
    assert out["ok"] is False and "internal error" in out["detail"]

# Review Finding 4: atomic config write
def test_config_set_atomic_no_tmp_leftover(tmp_path):
    """config.set must write atomically; no .tmp file should remain after."""
    ctx, _ = make_ctx(tmp_path)
    handle_request({"op": "config.set",
                    "args": {"key": "bridge.agent_port", "value": 9999}}, ctx)
    # Verify config file exists and parses
    assert ctx.config_path.exists()
    data = json.loads(ctx.config_path.read_text())
    assert data["bridge.agent_port"] == 9999
    # Verify no .tmp file lingering
    tmp_file = ctx.config_path.parent / f".{ctx.config_path.name}.tmp"
    assert not tmp_file.exists()
