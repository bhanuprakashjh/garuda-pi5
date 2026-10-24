import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import probes, portal

FAKE = {
    ("vcgencmd", "get_throttled"): "throttled=0x0\n",
    ("vcgencmd", "measure_temp"): "temp=48.2'C\n",
    ("hailortcli", "fw-control", "identify"): "Device Architecture: HAILO10H\n",
    ("systemctl", "is-active", "garuda-portal"): "active\n",
    ("systemctl", "is-active", "garuda-configd"): "active\n",
    ("systemctl", "is-active", "garuda-ptp4l-client"): "inactive\n",
    ("systemctl", "is-active", "garuda-mono"): "inactive\n",
}
def fake_run(cmd):
    return FAKE.get(tuple(cmd), "")

def test_collect_health_parses_probes():
    h = probes.collect_health(run=fake_run)
    assert h["throttled"] == "0x0"
    assert h["temp_c"] == 48.2
    assert h["npu"] == "HAILO10H"
    assert h["services"]["garuda-portal"] is True
    assert h["services"]["garuda-configd"] is True
    assert h["services"]["garuda-ptp4l-client"] is False
    assert h["services"]["garuda-mono"] is False

def test_collect_health_reports_cpu_and_load():
    h = probes.collect_health(run=fake_run)
    assert "cpu_pct" in h and "cpu_cores" in h and "loadavg" in h and "pcie" in h
    if h["cpu_pct"] is not None:
        assert 0.0 <= h["cpu_pct"] <= 100.0
    if h["cpu_cores"] is not None:
        assert all(0.0 <= c <= 100.0 for c in h["cpu_cores"])
    # pcie is None off-device (no Hailo on the dev PC) or a link dict on the bench
    if h["pcie"] is not None:
        assert set(h["pcie"]) >= {"gen", "width", "gbps"}

def test_collect_health_survives_missing_tools():
    h = probes.collect_health(run=lambda cmd: (_ for _ in ()).throw(FileNotFoundError()))
    assert h["throttled"] == "unknown" and h["npu"] is None

def test_collect_health_survives_filesystem_errors():
    import shutil, unittest.mock
    with unittest.mock.patch("shutil.disk_usage", side_effect=OSError("no disk")):
        with unittest.mock.patch("builtins.open", side_effect=IOError("no uptime")):
            h = probes.collect_health(run=fake_run)
            assert h["disk_free_gb"] is None
            assert h["uptime_s"] is None
            assert h["throttled"] == "0x0"

def test_collect_logs_returns_runner_output():
    out = probes.collect_logs(run=lambda cmd: "line1\nline2\n", n=200)
    assert out == "line1\nline2\n"

def test_collect_logs_falls_back_when_runner_raises():
    def raising(cmd):
        raise FileNotFoundError("journalctl not found")
    assert probes.collect_logs(run=raising) == "logs unavailable"

def test_collect_logs_falls_back_on_empty_output():
    assert probes.collect_logs(run=lambda cmd: "") == "logs unavailable"

def test_api_health_with_query_string():
    import threading, urllib.request, json, time
    server = portal.build_server(8098, {"health": lambda: {"ok": 1}})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:8098/api/health?t=123")
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data == {"ok": 1}
    finally:
        server.shutdown()
