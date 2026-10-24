import json, socket, sys, threading, pathlib, time, urllib.request, urllib.error
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import configd_client
import portal

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

# --- config_get and config_set helper functions ---

def test_config_get_passes_through_to_injected_call(tmp_path):
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": True, "detail": "ok", "result": {"bridge.agent_ip": "192.168.1.1"}})
    result = portal.config_get(call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(sp)))
    assert result["ok"] is True
    assert result["result"]["bridge.agent_ip"] == "192.168.1.1"

def test_config_set_passes_through_to_injected_call(tmp_path):
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": True, "detail": "ok", "result": {"ptp.enabled": False}})
    result = portal.config_set({"key": "ptp.enabled", "value": False},
                               call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(sp)))
    assert result["ok"] is True

def test_config_set_conveys_rejection(tmp_path):
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": False, "detail": "invalid value for 'bridge.agent_port'", "result": None})
    result = portal.config_set({"key": "bridge.agent_port", "value": 999999},
                               call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(sp)))
    assert result["ok"] is False
    assert "invalid value" in result["detail"]

# --- HTTP-level tests for GET/PUT /api/config ---

def _start_server(deps, port):
    server = portal.build_server(port, deps)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    return server

def test_get_api_config_returns_store_via_configd(tmp_path):
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": True, "detail": "ok",
                      "result": {"bridge.agent_ip": "192.168.1.1", "ptp.enabled": True}})

    def config_get_handler(handler):
        handler._json(portal.config_get(call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(sp))))

    deps = {"health": lambda: {}, "get_routes": {"/api/config": config_get_handler}}
    server = _start_server(deps, 8106)
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:8106/api/config")
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["ok"] is True
        assert data["result"]["ptp.enabled"] is True
    finally:
        server.shutdown()

def _put_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="PUT")
    resp = urllib.request.urlopen(req)
    return resp.status, json.loads(resp.read())

def test_put_api_config_whitelisted_key_ok(tmp_path):
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": True, "detail": "ok", "result": {"ptp.enabled": False}})

    def config_set_handler(handler, body):
        handler._json(portal.config_set(body, call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(sp))))

    deps = {"health": lambda: {}, "post_routes": {"/api/config": config_set_handler}}
    server = _start_server(deps, 8107)
    try:
        status, data = _put_json("http://127.0.0.1:8107/api/config",
                                 {"key": "ptp.enabled", "value": False})
        assert status == 200
        assert data["ok"] is True
    finally:
        server.shutdown()

def test_put_api_config_nonwhitelisted_key_conveys_rejection(tmp_path):
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": False, "detail": "config key not whitelisted: 'bogus_key'", "result": None})

    def config_set_handler(handler, body):
        handler._json(portal.config_set(body, call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(sp))))

    deps = {"health": lambda: {}, "post_routes": {"/api/config": config_set_handler}}
    server = _start_server(deps, 8108)
    try:
        status, data = _put_json("http://127.0.0.1:8108/api/config",
                                 {"key": "bogus_key", "value": "something"})
        assert status == 200
        assert data["ok"] is False
        assert "not whitelisted" in data["detail"]
    finally:
        server.shutdown()

# --- Test for query-string stripping fix (hygiene item) ---

def test_post_with_query_string_still_dispatches(tmp_path):
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": True, "detail": "ok", "result": {"ptp.enabled": False}})

    def config_set_handler(handler, body):
        handler._json(portal.config_set(body, call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(sp))))

    deps = {"health": lambda: {}, "post_routes": {"/api/config": config_set_handler}}
    server = _start_server(deps, 8109)
    try:
        # POST /api/config?foo=bar should still work (query string stripped)
        req = urllib.request.Request("http://127.0.0.1:8109/api/config?foo=bar",
                                     data=json.dumps({"key": "ptp.enabled", "value": False}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        resp = urllib.request.urlopen(req)
        status = resp.status
        data = json.loads(resp.read())
        assert status == 200
        assert data["ok"] is True
    finally:
        server.shutdown()

# --- Test type coercion: bridge.agent_port must be sent as int, not string ---

def test_put_api_config_number_input_sent_as_int_type(tmp_path):
    """Verify HTTP-level behavior: number inputs coerced to int before configd.
    The stub configd captures the request to inspect that port value is int type."""
    sp = tmp_path / "c.sock"
    received_request = []

    def stub_with_capture(sock_path):
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path)); srv.listen(1)
        def run():
            conn, _ = srv.accept()
            line = conn.makefile("r").readline()
            received_request.append(json.loads(line))
            conn.sendall(json.dumps({"ok": True, "detail": "ok", "result": {}}).encode() + b"\n")
            conn.close()
        threading.Thread(target=run, daemon=True).start()
        return srv

    stub_with_capture(sp)

    def config_set_handler(handler, body):
        handler._json(portal.config_set(body, call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(sp))))

    deps = {"health": lambda: {}, "post_routes": {"/api/config": config_set_handler}}
    server = _start_server(deps, 8110)
    try:
        status, data = _put_json("http://127.0.0.1:8110/api/config",
                                 {"key": "bridge.agent_port", "value": 8080})
        assert status == 200
        assert data["ok"] is True
        # Verify request to configd has port as int, not string
        assert len(received_request) > 0
        req = received_request[0]
        assert req["args"]["value"] == 8080
        assert isinstance(req["args"]["value"], int)
    finally:
        server.shutdown()

# --- F3: configd-down window must degrade gracefully, not raise -------------

def test_get_api_config_configd_unavailable_returns_ok_false_no_exception(tmp_path):
    """GET /api/config against a bogus configd socket path: no exception should
    escape config_get/the handler; the HTTP response is a graceful ok:false."""
    bogus_sock = tmp_path / "does-not-exist.sock"

    def config_get_handler(handler):
        handler._json(portal.config_get(
            call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(bogus_sock))))

    deps = {"health": lambda: {}, "get_routes": {"/api/config": config_get_handler}}
    server = _start_server(deps, 8111)
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:8111/api/config")
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data["ok"] is False
        assert "unavailable" in data["detail"]
    finally:
        server.shutdown()

def test_put_api_config_configd_unavailable_returns_ok_false_no_exception(tmp_path):
    """PUT /api/config against a bogus configd socket path: same graceful
    ok:false contract on the config_set side."""
    bogus_sock = tmp_path / "does-not-exist.sock"

    def config_set_handler(handler, body):
        handler._json(portal.config_set(
            body, call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(bogus_sock))))

    deps = {"health": lambda: {}, "post_routes": {"/api/config": config_set_handler}}
    server = _start_server(deps, 8112)
    try:
        status, data = _put_json("http://127.0.0.1:8112/api/config",
                                 {"key": "ptp.enabled", "value": False})
        assert status == 200
        assert data["ok"] is False
        assert "unavailable" in data["detail"]
    finally:
        server.shutdown()

# --- F4: malformed POST body/Content-Length must 400, not crash the conn ---

def test_post_invalid_json_body_returns_400_and_server_stays_responsive(tmp_path):
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": True, "detail": "ok", "result": {"ptp.enabled": False}})

    def config_set_handler(handler, body):
        handler._json(portal.config_set(
            body, call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(sp))))

    deps = {"health": lambda: {}, "post_routes": {"/api/config": config_set_handler}}
    server = _start_server(deps, 8113)
    try:
        req = urllib.request.Request("http://127.0.0.1:8113/api/config",
                                     data=b"{not valid json",
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req)
            assert False, "expected HTTP 400 for malformed JSON body"
        except urllib.error.HTTPError as e:
            assert e.code == 400
            assert json.loads(e.read()) == {"error": "bad request"}

        # Server must still be responsive to a subsequent well-formed request.
        status, data = _put_json("http://127.0.0.1:8113/api/config",
                                 {"key": "ptp.enabled", "value": False})
        assert status == 200
        assert data["ok"] is True
    finally:
        server.shutdown()

def test_post_bad_content_length_returns_400_and_server_stays_responsive(tmp_path):
    """urllib.request manages Content-Length itself, so this goes over a raw
    socket to actually send a non-numeric header value — the real shape of
    the bug (int(self.headers.get("Content-Length", 0)) unguarded)."""
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": True, "detail": "ok", "result": {"ptp.enabled": False}})

    def config_set_handler(handler, body):
        handler._json(portal.config_set(
            body, call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(sp))))

    deps = {"health": lambda: {}, "post_routes": {"/api/config": config_set_handler}}
    server = _start_server(deps, 8114)
    try:
        raw_body = json.dumps({"key": "ptp.enabled", "value": False}).encode()
        request = (b"POST /api/config HTTP/1.1\r\n"
                   b"Host: 127.0.0.1\r\n"
                   b"Content-Type: application/json\r\n"
                   b"Content-Length: not-a-number\r\n"
                   b"Connection: close\r\n"
                   b"\r\n" + raw_body)
        with socket.create_connection(("127.0.0.1", 8114), timeout=5) as sock:
            sock.sendall(request)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            resp = b"".join(chunks).decode(errors="replace")
        assert " 400 " in resp.splitlines()[0]
        assert '{"error": "bad request"}' in resp

        # Server must still be responsive to a subsequent well-formed request.
        status, data = _put_json("http://127.0.0.1:8114/api/config",
                                 {"key": "ptp.enabled", "value": False})
        assert status == 200
        assert data["ok"] is True
    finally:
        server.shutdown()

def test_post_invalid_utf8_body_returns_400_and_server_stays_responsive(tmp_path):
    """json.loads() on bytes with invalid UTF-8 raises UnicodeDecodeError
    (a sibling of JSONDecodeError under ValueError). The handler must catch
    and return 400 for this case too, not crash the connection."""
    sp = tmp_path / "c.sock"
    stub_configd(sp, {"ok": True, "detail": "ok", "result": {"ptp.enabled": False}})

    def config_set_handler(handler, body):
        handler._json(portal.config_set(
            body, call=lambda op, args, **kw: configd_client.call(op, args, sock_path=str(sp))))

    deps = {"health": lambda: {}, "post_routes": {"/api/config": config_set_handler}}
    server = _start_server(deps, 8115)
    try:
        raw_body = b"\xff\xfe\x00\x01bad-utf16-payload"
        request = (b"POST /api/config HTTP/1.1\r\n"
                   b"Host: 127.0.0.1\r\n"
                   b"Content-Type: application/json\r\n"
                   b"Content-Length: " + str(len(raw_body)).encode() + b"\r\n"
                   b"Connection: close\r\n"
                   b"\r\n" + raw_body)
        with socket.create_connection(("127.0.0.1", 8115), timeout=5) as sock:
            sock.sendall(request)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            resp = b"".join(chunks).decode(errors="replace")
        assert " 400 " in resp.splitlines()[0]
        assert '{"error": "bad request"}' in resp

        # Server must still be responsive to a subsequent well-formed request.
        status, data = _put_json("http://127.0.0.1:8115/api/config",
                                 {"key": "ptp.enabled", "value": False})
        assert status == 200
        assert data["ok"] is True
    finally:
        server.shutdown()
