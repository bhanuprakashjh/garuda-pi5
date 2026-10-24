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
    except FileNotFoundError: return {}
    except json.JSONDecodeError:
        print("[configd] WARN corrupt config, starting empty", file=sys.stderr, flush=True)
        return {}

def _audit(tag: str, req: dict, detail: str):
    print(f"[configd] {tag} {json.dumps(req, separators=(',', ':'))[:300]} :: {detail}",
          file=sys.stderr, flush=True)

def handle_request(req: dict, ctx: Ctx) -> dict:
    ok, why = validate_request(req)
    if not ok:
        _audit("REJECT", req, why)
        return {"ok": False, "detail": why, "result": None}
    op, args = req["op"], req.get("args", {})
    try:
        if op == "config.get":
            _audit("ACCEPT", req, "get")
            return {"ok": True, "detail": "ok", "result": _load(ctx.config_path)}
        if op == "config.set":
            store = apply_config_set(_load(ctx.config_path), args["key"], args["value"])
            ctx.config_path.parent.mkdir(parents=True, exist_ok=True)
            tmpfile = ctx.config_path.parent / f".{ctx.config_path.name}.tmp"
            tmpfile.write_text(json.dumps(store, indent=2))
            os.replace(str(tmpfile), str(ctx.config_path))
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
    except Exception as e:
        _audit("REJECT", req, f"internal error: {type(e).__name__}")
        return {"ok": False, "detail": f"internal error: {type(e).__name__}", "result": None}

def _real_ctx() -> Ctx:
    return Ctx(
        config_path=CONFIG_PATH,
        run_unit=lambda u, a: subprocess.run(["systemctl", a, u], check=False),
        run_system=lambda a: subprocess.run(["systemctl", a], check=False))

def serve_connection(conn, ctx: Ctx):
    """Handle one client connection entirely inside exception guards; never propagate."""
    try:
        conn.settimeout(10.0)
        data = conn.makefile("r").readline()
        if not data:
            _audit("REJECT", {"raw": ""}, "empty line")
            reply = {"ok": False, "detail": "empty line", "result": None}
        else:
            reply = handle_request(json.loads(data), ctx)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        _audit("REJECT", {"raw": data[:120] if 'data' in locals() else ""}, f"malformed: {type(e).__name__}")
        reply = {"ok": False, "detail": f"bad request: {e}", "result": None}
    except (OSError, socket.timeout) as e:
        _audit("REJECT", {"raw": data[:120] if 'data' in locals() else ""}, f"socket error: {type(e).__name__}")
        return
    except Exception as e:
        _audit("REJECT", {"raw": data[:120] if 'data' in locals() else ""}, f"internal: {type(e).__name__}")
        reply = {"ok": False, "detail": f"internal error: {type(e).__name__}", "result": None}
    try:
        conn.sendall((json.dumps(reply) + "\n").encode())
    except (OSError, socket.timeout) as e:
        _audit("REJECT", {"raw": ""}, f"sendall failed: {type(e).__name__}")

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
            serve_connection(conn, ctx)

if __name__ == "__main__":
    main()
