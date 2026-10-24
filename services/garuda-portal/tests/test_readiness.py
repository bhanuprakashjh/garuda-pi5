import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from readiness import run_checks

HEALTHY = {"throttled": "0x0", "temp_c": 50.0, "npu": "HAILO10H",
           "services": {"garuda-portal": True, "garuda-configd": True,
                        "garuda-ptp4l-client": True},
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

def test_disk_free_gb_none_returns_na_no_exception():
    """CRITICAL: disk_free_gb=None must not raise TypeError; should return "na"."""
    r = run_checks({**HEALTHY, "disk_free_gb": None}, {})
    storage = next(c for c in r["checks"] if c["id"] == "storage.space")
    assert storage["status"] == "na"
    assert r["overall"] == "go"  # Only power.supply5a warn, no fail/other warn

def test_fully_degraded_health_no_exception():
    """CRITICAL: fully degraded health dict (all None/empty) must return report, overall no-go."""
    degraded = {
        "throttled": "unknown",
        "temp_c": None,
        "npu": None,
        "services": {},
        "disk_free_gb": None,
        "uptime_s": None
    }
    r = run_checks(degraded, {})
    assert "overall" in r
    assert r["overall"] == "no-go"  # npu.identify fail dominates
    assert len(r["checks"]) > 0
    # Verify no exception was raised (this test would crash if it was)
