import sys, pathlib, json
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
cv2 = pytest.importorskip("cv2")
from calibration import CoverageGrid, solve, find_corners, CalibSession

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

# --- CalibSession: session/route layer (Step 5) -----------------------------

class FakeStream:
    """Stands in for camera_stream.CameraStream: only .latest matters here."""
    def __init__(self, latest=None):
        self.latest = latest

def _board_gray():
    """Same synthetic 9x6-corner board as test_find_corners_on_synthetic_board,
    factored out so the session-layer tests can encode it to a real JPEG."""
    board = np.full((480, 640), 255, np.uint8)
    sq = 40
    for r in range(7):
        for c in range(10):
            if (r + c) % 2 == 0:
                board[40 + r * sq:40 + (r + 1) * sq, 60 + c * sq:60 + (c + 1) * sq] = 0
    return board

def _board_jpeg():
    ok, buf = cv2.imencode(".jpg", _board_gray())
    assert ok
    return buf.tobytes()

def _synthetic_calib_points(pattern=(9, 6), square_size_m=0.025, n=12):
    """Same construction as test_solve_recovers_known_camera, factored out so
    the solve_and_save tests can seed a session without a corner-detection
    pass (that path is already covered by the capture tests below)."""
    K = np.array([[600.0, 0, 320], [0, 600.0, 240], [0, 0, 1]])
    cols, rows = pattern
    obj = np.zeros((cols * rows, 3), np.float32)
    obj[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square_size_m
    objpoints, imgpoints = [], []
    rng = np.random.default_rng(7)
    for i in range(n):
        rvec = rng.normal(0, 0.2, 3); tvec = np.array([0.05, 0.05, 0.5 + 0.05 * i])
        img, _ = cv2.projectPoints(obj, rvec, tvec, K, None)
        objpoints.append(obj); imgpoints.append(img.astype(np.float32))
    return objpoints, imgpoints

def test_calib_session_reset_sets_pattern_and_clears_state():
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    assert session.pattern == (9, 6)
    assert session.square_size_m == 0.025
    assert session.grid.complete is False
    assert session.objpoints == []
    assert session.imgpoints == []

def test_capture_no_frame_returns_informative_not_ok():
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    result = session.capture(FakeStream(latest=None))
    assert result == {"ok": False, "detail": "no frame (camera busy or not started)"}

def test_capture_no_board_in_frame_reports_not_found():
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    blank = np.full((480, 640), 200, np.uint8)
    ok, buf = cv2.imencode(".jpg", blank)
    assert ok
    result = session.capture(FakeStream(latest=buf.tobytes()))
    assert result["ok"] is True
    assert result["corners_found"] is False
    assert result["added"] is False
    assert session.imgpoints == []

def test_capture_finds_corners_and_accumulates_points():
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    result = session.capture(FakeStream(latest=_board_jpeg()))
    assert result["ok"] is True
    assert result["corners_found"] is True
    assert result["added"] is True
    assert len(session.imgpoints) == 1
    assert len(session.objpoints) == 1
    assert session.image_size == (640, 480)

def test_repeated_capture_in_same_zone_stops_accumulating_once_full():
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    frame = _board_jpeg()
    added_sequence = [session.capture(FakeStream(latest=frame))["added"] for _ in range(4)]
    # default per_zone=3: the same centroid fills its zone on captures 1-3,
    # the 4th capture lands in an already-full zone.
    assert added_sequence == [True, True, True, False]
    assert len(session.imgpoints) == 3  # the 4th (rejected) capture wasn't accumulated

def test_solve_and_save_requires_min_9_captures(tmp_path):
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    result = session.solve_and_save("imx708.cam0", calib_dir=str(tmp_path))
    assert result["ok"] is False
    assert "9" in result["detail"]
    assert not (tmp_path / "imx708.cam0.json").exists()

def test_solve_and_save_writes_json_and_history(tmp_path):
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    session.objpoints, session.imgpoints = _synthetic_calib_points()
    session.image_size = (640, 480)

    result = session.solve_and_save("imx708.cam0", calib_dir=str(tmp_path))

    assert result["ok"] is True
    assert result["rms"] < 0.1
    calib_file = tmp_path / "imx708.cam0.json"
    assert calib_file.exists()
    saved = json.loads(calib_file.read_text())
    assert saved["pattern"] == [9, 6]
    assert saved["square_size_m"] == 0.025
    assert "t_saved_utc" in saved
    assert saved["rms"] == result["rms"]

    history_file = tmp_path / "history.jsonl"
    assert history_file.exists()
    lines = history_file.read_text().strip().splitlines()
    assert len(lines) == 1
    hist_record = json.loads(lines[0])
    assert hist_record["rms"] == result["rms"]

def test_calib_status_reports_session_state():
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    status = session.status()
    assert status["pattern"] == [9, 6]
    assert status["square_size_m"] == 0.025
    assert status["captures"] == 0
    assert status["complete"] is False
    assert status["zone_counts"] == [[0, 0, 0], [0, 0, 0], [0, 0, 0]]

# --- HTTP-level: status/capture/solve wired exactly as main() wires them ----

def _post_json(url, payload):
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    resp = urllib.request.urlopen(req)
    return resp.status, json.loads(resp.read())

# --- Review fixes: locking, path traversal, degenerate solve, bad decode ----

def test_capture_bad_jpeg_bytes_returns_could_not_decode():
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    result = session.capture(FakeStream(latest=b"definitely not a jpeg, just garbage bytes"))
    assert result == {"ok": False, "detail": "could not decode frame"}

def test_solve_catches_cv2_errors_and_reports_gracefully():
    """cv2.calibrateCamera raises cv2.error on malformed input (e.g. a point
    count mismatch between obj/img points) rather than returning a dict —
    solve() must catch that and report failure instead of propagating."""
    obj = np.zeros((9 * 6, 3), np.float32)
    objpoints = [obj] * 9
    imgpoints = [np.zeros((5, 1, 2), np.float32)] * 9  # wrong point count vs obj
    result = solve(objpoints, imgpoints, (640, 480))
    assert result["ok"] is False
    assert "solve failed" in result["detail"]

def test_solve_and_save_propagates_solve_failure_without_writing_files(tmp_path):
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    obj = np.zeros((9 * 6, 3), np.float32)
    session.objpoints = [obj] * 9
    session.imgpoints = [np.zeros((5, 1, 2), np.float32)] * 9  # malformed
    session.image_size = (640, 480)

    result = session.solve_and_save("imx708.cam0", calib_dir=str(tmp_path))

    assert result["ok"] is False
    assert "solve failed" in result["detail"]
    assert not (tmp_path / "imx708.cam0.json").exists()
    assert not (tmp_path / "history.jsonl").exists()

def test_solve_and_save_rejects_path_traversal_sensor_name(tmp_path):
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    session.objpoints, session.imgpoints = _synthetic_calib_points()
    session.image_size = (640, 480)

    result = session.solve_and_save("../../../etc/passwd", calib_dir=str(tmp_path))

    assert result == {"ok": False, "detail": "invalid sensor name"}
    assert list(tmp_path.iterdir()) == []  # nothing written, nothing escaped calib_dir

def test_solve_and_save_rejects_sensor_with_dotdot_even_without_slash(tmp_path):
    """Regex alone would accept '..' (both chars are individually allowed),
    so '..' must be rejected explicitly regardless of slashes."""
    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    session.objpoints, session.imgpoints = _synthetic_calib_points()
    session.image_size = (640, 480)

    result = session.solve_and_save("..", calib_dir=str(tmp_path))

    assert result == {"ok": False, "detail": "invalid sensor name"}
    assert list(tmp_path.iterdir()) == []

def test_calib_solve_route_rejects_path_traversal_sensor_http_level(tmp_path):
    """End-to-end through the actual portal.py wiring shape: the handler
    forwards body['sensor'] straight to solve_and_save, which must be the
    choke point that refuses to write outside calib_dir."""
    import threading, time
    import portal

    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    session.objpoints, session.imgpoints = _synthetic_calib_points()
    session.image_size = (640, 480)

    def solve_handler(handler, body):
        sensor = body.get("sensor", "imx708.cam0")
        handler._json(session.solve_and_save(sensor, calib_dir=str(tmp_path)))

    deps = {"health": lambda: {}, "post_routes": {"/api/actions/calib-solve": solve_handler}}
    server = portal.build_server(8200, deps)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    try:
        status, data = _post_json("http://127.0.0.1:8200/api/actions/calib-solve",
                                  {"sensor": "../../../etc/passwd"})
        assert status == 200
        assert data == {"ok": False, "detail": "invalid sensor name"}
        assert list(tmp_path.iterdir()) == []
    finally:
        server.shutdown()

def test_concurrent_capture_and_solve_keep_point_arrays_in_sync(tmp_path):
    """IMPORTANT-1 fix: the portal is a ThreadingHTTPServer, so capture/solve/
    status/reset can be invoked concurrently from the browser. CalibSession
    must guard its internal state with a lock so objpoints/imgpoints are
    always appended/read as a consistent pair — never observed mismatched in
    length, and no exception escapes from the race."""
    import threading

    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    seed_obj, seed_img = _synthetic_calib_points(n=12)
    session.objpoints, session.imgpoints = list(seed_obj), list(seed_img)
    session.image_size = (640, 480)

    stream = FakeStream(latest=_board_jpeg())
    errors = []
    violations = []
    stop = threading.Event()

    def capture_worker():
        for _ in range(30):
            try:
                session.capture(stream)
            except Exception as e:
                errors.append(e)

    def solve_worker():
        for _ in range(30):
            try:
                session.solve_and_save("imx708.cam0", calib_dir=str(tmp_path))
            except Exception as e:
                errors.append(e)

    def checker_worker():
        # Reads through the same lock so it can never observe a torn
        # intermediate state between the two appends in capture(). Any
        # exception here (e.g. missing lock) must fail the test, not just
        # emit a background-thread warning pytest would otherwise swallow.
        try:
            while not stop.is_set():
                with session._lock:
                    if len(session.objpoints) != len(session.imgpoints):
                        violations.append((len(session.objpoints), len(session.imgpoints)))
        except Exception as e:
            errors.append(e)

    workers = [threading.Thread(target=capture_worker) for _ in range(2)]
    workers.append(threading.Thread(target=solve_worker))
    checker = threading.Thread(target=checker_worker)
    checker.start()
    for t in workers:
        t.start()
    for t in workers:
        t.join(timeout=15)
    stop.set()
    checker.join(timeout=2)

    assert errors == []
    assert violations == []

def test_calib_routes_http_level(tmp_path):
    import threading, time, urllib.request
    import portal

    session = CalibSession()
    session.reset(pattern=(9, 6), square_size_m=0.025)
    stream = FakeStream(latest=None)

    def capture_handler(handler, body):
        handler._json(session.capture(stream))
    def solve_handler(handler, body):
        handler._json(session.solve_and_save("imx708.cam0", calib_dir=str(tmp_path)))
    def status_handler(handler):
        handler._json(session.status())

    deps = {
        "health": lambda: {},
        "get_routes": {"/api/calibration/status": status_handler},
        "post_routes": {
            "/api/actions/calib-capture": capture_handler,
            "/api/actions/calib-solve": solve_handler,
        },
    }
    server = portal.build_server(8199, deps)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:8199/api/calibration/status")
        assert json.loads(resp.read())["captures"] == 0

        status, data = _post_json("http://127.0.0.1:8199/api/actions/calib-capture", {})
        assert status == 200
        assert data["ok"] is False  # no frame yet

        stream.latest = _board_jpeg()
        status, data = _post_json("http://127.0.0.1:8199/api/actions/calib-capture", {})
        assert data["ok"] is True
        assert data["corners_found"] is True

        status, data = _post_json("http://127.0.0.1:8199/api/actions/calib-solve", {})
        assert data["ok"] is False  # still short of 9 captures
        assert "9" in data["detail"]
    finally:
        server.shutdown()
