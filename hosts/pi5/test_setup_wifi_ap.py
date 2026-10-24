import os
import pathlib
import subprocess

SCRIPT = str(pathlib.Path(__file__).resolve().parent / "setup_wifi_ap.sh")


def run(*args):
    return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True)


def test_requires_psk():
    r = run("--dry-run")
    assert r.returncode == 2
    assert "--psk" in r.stderr


def test_rejects_short_psk():
    r = run("--dry-run", "--psk", "short")
    assert r.returncode == 2


def test_dry_run_full_sequence_5ghz_defaults():
    r = run("--dry-run", "--psk", "testpass123")
    assert r.returncode == 0
    out = r.stdout
    assert "DRY: sudo raspi-config nonint do_wifi_country US" in out
    assert "DRY: sudo nmcli radio wifi on" in out
    assert "802-11-wireless.mode ap" in out
    assert "802-11-wireless.band a" in out
    assert "802-11-wireless.channel 36" in out
    assert "ipv4.method shared" in out
    assert "ipv4.addresses 10.43.0.1/24" in out
    assert "wifi-sec.key-mgmt wpa-psk" in out
    assert "wifi-sec.psk testpass123" in out
    assert "connection.autoconnect yes" in out
    assert "DRY: sudo nmcli connection up garuda-ap" in out
    assert "connection modify" not in out


def test_band_bg_uses_channel_6():
    r = run("--dry-run", "--psk", "testpass123", "--band", "bg")
    assert r.returncode == 0
    assert "802-11-wireless.band bg" in r.stdout
    assert "802-11-wireless.channel 6" in r.stdout


def test_rejects_unknown_band():
    r = run("--dry-run", "--psk", "testpass123", "--band", "n")
    assert r.returncode == 2


def test_dry_run_modify_path_reasserts_full_property_set():
    r = subprocess.run(["bash", SCRIPT, "--dry-run", "--psk", "testpass123"],
                       capture_output=True, text=True,
                       env={**os.environ, "DRY_PROFILE_EXISTS": "1"})
    assert r.returncode == 0
    out = r.stdout
    assert "DRY: sudo nmcli connection modify garuda-ap" in out
    assert "connection add" not in out
    for prop in ("802-11-wireless.mode ap", "ipv4.method shared",
                 "ipv4.addresses 10.43.0.1/24", "ipv6.method disabled",
                 "wifi-sec.key-mgmt wpa-psk", "wifi-sec.psk testpass123",
                 "802-11-wireless.band a", "802-11-wireless.channel 36"):
        assert prop in out, prop
