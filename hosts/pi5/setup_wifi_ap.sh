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

profile_exists() {
  if [ "$DRY" = 1 ]; then [ "${DRY_PROFILE_EXISTS:-0}" = 1 ]; return; fi
  nmcli -t -f NAME connection show 2>/dev/null | grep -qx "garuda-ap"
}

run sudo raspi-config nonint do_wifi_country "$COUNTRY"
run sudo nmcli radio wifi on

# check-then-modify (rule 3): update the existing profile rather than duplicate it.
# On hosts without nmcli (x86 CI dry-run) the check fails closed into the add path.
# profile_exists() is injectable via DRY_PROFILE_EXISTS for deterministic CI testing.
if profile_exists; then
  run sudo nmcli connection modify garuda-ap \
    connection.autoconnect yes \
    802-11-wireless.ssid "$SSID" 802-11-wireless.mode ap \
    802-11-wireless.band "$BAND" 802-11-wireless.channel "$CHANNEL" \
    ipv4.method shared ipv4.addresses 10.43.0.1/24 ipv6.method disabled \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK"
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
