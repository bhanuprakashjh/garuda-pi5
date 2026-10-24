# Pi WiFi Hotspot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any PC/phone joins the Pi's own WPA2 hotspot and opens the portal at `http://10.43.0.1:8080` — no router, no cable; Ethernet dev link untouched.

**Architecture:** One idempotent host script (`hosts/pi5/setup_wifi_ap.sh`) drives NetworkManager: set WiFi country, radio on, create-or-update AP profile `garuda-ap` on wlan0 (5 GHz WPA2, `ipv4.method shared` at 10.43.0.1/24, autoconnect). No service code changes — portal and mono already bind 0.0.0.0.

**Tech Stack:** bash + nmcli + raspi-config (all pre-installed), pytest (dry-run CI test).

**Spec:** `docs/superpowers/specs/2026-08-09-pi-wifi-hotspot-design.md`

## Global Constraints

- Branch: `feat/portal-tracking`, worktree `/media/bhanu1234/Development/GARUDASDK/.claude/worktrees/feat+portal-tracking`.
- Rule 3: the script must be idempotent — check-then-modify, safe to re-run, updates (never duplicates) the `garuda-ap` profile.
- Rule 10: running the script on the Pi is a persistent network-config change (country code, autoconnect AP profile) — the controller confirms the owner's go in-session immediately before Task 2 Step 1 runs.
- The passphrase is passed at run time and lives ONLY in the Pi's NetworkManager config — it must NEVER appear in any committed file (plan, spec, script default, test, docs). Tests use the dummy `testpass123`.
- Hotspot subnet is 10.43.0.1/24 — must NOT be 10.42.0.x (wired link's subnet).
- The bench is power-limited (15 W ceiling until the 27 W PSU arrives): any step that reboots the Pi must stop `garuda-mono` immediately after it returns (it auto-starts on boot and browns the rail).
- No new dependencies. Pi deploy for host scripts = `scp` to `garuda@10.42.0.128:~/garuda-sdk-deploy/<same relative path>`.

---

### Task 1: setup_wifi_ap.sh + dry-run CI test

**Files:**
- Create: `hosts/pi5/setup_wifi_ap.sh`
- Test: `hosts/pi5/test_setup_wifi_ap.py`

**Interfaces:**
- Produces: `setup_wifi_ap.sh --psk <pass> [--ssid GarudaBench] [--country US] [--band a|bg] [--dry-run]`; `--dry-run` prints each privileged command prefixed `DRY: ` and is the CI test surface. Task 2 runs this script on the Pi verbatim.

- [ ] **Step 1: Write the failing tests**

`hosts/pi5/test_setup_wifi_ap.py`:

```python
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


def test_band_bg_uses_channel_6():
    r = run("--dry-run", "--psk", "testpass123", "--band", "bg")
    assert r.returncode == 0
    assert "802-11-wireless.band bg" in r.stdout
    assert "802-11-wireless.channel 6" in r.stdout


def test_rejects_unknown_band():
    r = run("--dry-run", "--psk", "testpass123", "--band", "n")
    assert r.returncode == 2
```

- [ ] **Step 2: Run to verify failure** — `cd hosts/pi5 && python3 -m pytest test_setup_wifi_ap.py -v` → all FAIL (script missing).

- [ ] **Step 3: Implement the script**

`hosts/pi5/setup_wifi_ap.sh` (then `chmod +x`):

```bash
#!/usr/bin/env bash
# setup_wifi_ap.sh — GarudaBench hotspot: NetworkManager AP mode on wlan0 (idempotent).
# Any WiFi client reaches the portal at http://10.43.0.1:8080 (mono debug :8098).
# 5 GHz AP channels require a set regulatory country. If a client cannot see the
# SSID, re-run with --band bg (2.4 GHz).
# The passphrase is stored only in NetworkManager's config on the Pi — never in git.
# Usage: setup_wifi_ap.sh --psk <pass> [--ssid GarudaBench] [--country US] [--band a|bg] [--dry-run]
set -euo pipefail

SSID="GarudaBench" COUNTRY="US" BAND="a" PSK="" DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --psk) PSK="$2"; shift 2 ;;
    --ssid) SSID="$2"; shift 2 ;;
    --country) COUNTRY="$2"; shift 2 ;;
    --band) BAND="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$PSK" ] || { echo "required: --psk <passphrase, >=8 chars>" >&2; exit 2; }
[ "${#PSK}" -ge 8 ] || { echo "--psk must be >=8 chars" >&2; exit 2; }
case "$BAND" in
  a) CHANNEL=36 ;;
  bg) CHANNEL=6 ;;
  *) echo "--band must be a or bg" >&2; exit 2 ;;
esac

run() { if [ "$DRY" = 1 ]; then echo "DRY: $*"; else "$@"; fi; }

run sudo raspi-config nonint do_wifi_country "$COUNTRY"
run sudo nmcli radio wifi on

# check-then-modify (rule 3): update the existing profile rather than duplicate it.
# On hosts without nmcli (x86 CI dry-run) the check fails closed into the add path.
if nmcli -t -f NAME connection show 2>/dev/null | grep -qx "garuda-ap"; then
  run sudo nmcli connection modify garuda-ap \
    802-11-wireless.ssid "$SSID" \
    802-11-wireless.band "$BAND" 802-11-wireless.channel "$CHANNEL" \
    wifi-sec.psk "$PSK" connection.autoconnect yes
else
  run sudo nmcli connection add type wifi ifname wlan0 con-name garuda-ap \
    connection.autoconnect yes \
    802-11-wireless.ssid "$SSID" 802-11-wireless.mode ap \
    802-11-wireless.band "$BAND" 802-11-wireless.channel "$CHANNEL" \
    ipv4.method shared ipv4.addresses 10.43.0.1/24 ipv6.method disabled \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK"
fi

run sudo nmcli connection up garuda-ap
echo "hotspot: SSID=$SSID band=$BAND ch=$CHANNEL — portal at http://10.43.0.1:8080"
```

- [ ] **Step 4: Run to verify pass** — same command → 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add hosts/pi5/setup_wifi_ap.sh hosts/pi5/test_setup_wifi_ap.py
git commit -m "feat(pi5): idempotent GarudaBench WiFi-AP setup script (dry-run CI-tested)"
```

---

### Task 2: run on the bench, verify from a client, docs close-out

**Files:**
- Modify (docs only): `docs/superpowers/specs/2026-08-09-pi-wifi-hotspot-design.md` (status line), `CLAUDE.md` (§7 one-liner)

- [ ] **Step 1: Owner gate + run (rule 10).** Controller confirms the owner's go in-session, then:

```bash
scp hosts/pi5/setup_wifi_ap.sh garuda@10.42.0.128:~/garuda-sdk-deploy/hosts/pi5/
ssh garuda@10.42.0.128 'bash ~/garuda-sdk-deploy/hosts/pi5/setup_wifi_ap.sh --psk <IN-SESSION-PSK> --country US'
```

(`<IN-SESSION-PSK>` = the passphrase agreed in-session — never committed; this placeholder is deliberate, rule: secrets stay out of git.)

Expected: final line `hotspot: SSID=GarudaBench band=a ch=36 — portal at http://10.43.0.1:8080`.

- [ ] **Step 2: Pi-side verification**

```bash
ssh garuda@10.42.0.128 'nmcli -t -f NAME,DEVICE,STATE connection show --active; iw dev wlan0 info | grep -E "type|channel"; ip -4 addr show wlan0 | grep inet'
```

Expected: `garuda-ap:wlan0:activated`; `type AP`; `inet 10.43.0.1/24`. Wired SSH still working (this very command proves it).

- [ ] **Step 3: Owner client test.** Owner joins `GarudaBench` from a phone/PC with the passphrase → opens `http://10.43.0.1:8080` → Overview loads; Tracking tab works (start mono via the button, see video, stop mono). Record the owner's confirmation.

- [ ] **Step 4: Reboot persistence (power-aware).** `ssh garuda@10.42.0.128 sudo reboot`, poll until it returns, then IMMEDIATELY `ssh garuda@10.42.0.128 sudo systemctl stop garuda-mono` (auto-starts on boot; the marginal rail browns out with it hot). Then confirm `garuda-ap` is active again without any command and a client can rejoin. Record.

- [ ] **Step 5: Docs + commit.** Spec status → `implemented 2026-08-09 (owner-verified)`. CLAUDE.md §7 bench-state paragraph: one sentence — hotspot `GarudaBench` live (NM AP profile `garuda-ap`, 10.43.0.1/24, autoconnect; portal reachable from any WiFi client; Ethernet dev link retained).

```bash
git add docs/superpowers/specs/2026-08-09-pi-wifi-hotspot-design.md CLAUDE.md
git commit -m "docs: WiFi hotspot close-out — spec implemented, CLAUDE.md bench state"
```
