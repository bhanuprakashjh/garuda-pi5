import pathlib


def test_run_track_script_is_guarded():
    s = (pathlib.Path(__file__).resolve().parents[3] / "hosts" / "pi5" / "run_track.sh").read_text()
    # frees the single-tenant camera/NPU, guarded (only stops active services)
    assert "garuda-depth" in s and "systemctl is-active" in s
    assert "pkill" in s                       # clears prior track/rpicam holders
    # no unguarded appends to any file (idempotency rule for hosts/pi5 — no cmdline/config edits)
    assert ">>" not in s
    assert "exec python3 track_service.py" in s
