# Pi WiFi hotspot — portal viewing from any PC — design spec

Date: 2026-08-09 · Status: implemented 2026-08-09 (owner-verified) · Branch: `feat/portal-tracking`

## Problem

The portal is only reachable over the bench Ethernet tether to the dev PC (10.42.0.x via the PC's
shared link). The owner wants to open the portal from any PC/phone with no router and no cable —
peer-to-peer to the Pi.

## Decision (owner-approved)

**The Pi hosts its own WPA2 hotspot** (NetworkManager AP mode on wlan0, SSID `GarudaBench`); any
device joins it and opens `http://10.43.0.1:8080` (mono debug at `:8098`). **Ethernet stays** as
the dev/deploy link (and the future PTP interface). True WiFi-Direct was rejected (poor PC client
support); joining an existing router was rejected (not peer-to-peer). Owner's regulatory country:
**US**. Passphrase agreed in-session; it lives ONLY in the Pi's NetworkManager config — never in
the repo.

## Mechanism

One idempotent script `hosts/pi5/setup_wifi_ap.sh` (rule 3: check-then-modify, safe to re-run):

1. Sets the WiFi regulatory country (`raspi-config nonint do_wifi_country`, required for 5 GHz AP
   channels — currently unset on the bench, radio disabled) and turns the radio on.
2. Creates-or-updates NetworkManager profile `garuda-ap` on `wlan0`: `mode ap`, band `a` (5 GHz,
   channel 36) by default with a `--band bg` (2.4 GHz, channel 6) fallback flag, WPA2-PSK,
   `autoconnect yes` (hotspot survives reboots), `ipv4.method shared` with static
   **10.43.0.1/24** — deliberately a different subnet from the wired 10.42.0.x link so the two
   interfaces never collide; NM's shared mode provides DHCP to clients.
3. Activates the profile. Re-running with different args updates the same profile (no duplicates).

Flags: `--psk` (required, ≥8 chars), `--ssid` (default `GarudaBench`), `--country` (default `US`),
`--band a|bg` (default `a`), `--dry-run` (print the privileged commands instead of running — also
the CI test surface).

No service code changes: portal (:8080) and mono (:8098) already bind `0.0.0.0` (verified
`portal.py:277`, `garuda_mono.py:523`), so they serve on the hotspot address as-is. avahi is
active on the Pi, so `http://raspberrypi.local:8080` also works from mDNS-capable clients
(hostname rename to `garuda` deliberately out of scope — separate owner call).

## Failure modes

- 5 GHz AP fails to start / a client can't see the SSID → re-run with `--band bg` (documented in
  the script header).
- Radio power cost on the power-limited bench (15 W ceiling until the 27 W PSU arrives): TBD-bench — measure as the vcgencmd pmic_read_adc EXT5V_A delta with the AP up vs down during Task 2 verification. The hotspot itself is near-idle load; mono stays under the manual power button's control.
- Hotspot misconfiguration cannot strand the bench: the Ethernet dev link is untouched, so SSH
  recovery is always available.

## Security

Anyone with the WPA2 passphrase reaches the unauthenticated portal (and mono's API) by radio.
Bench-tier mitigation: WPA2 with a generated non-dictionary passphrase, never committed. This
widens the exposure the pre-Phase-8 auth gate already covers; the gate (portal auth + non-sudo
account) remains the product-tier fix and is unchanged by this feature. Nothing here touches the
FC path (rule 12).

## Testing

- x86 CI: pytest runs the script with `--dry-run` and asserts the exact command sequence (country,
  radio, profile add/modify properties, activation) — no hardware, no root.
- Bench (owner-verified): script run on the Pi; a phone/PC joins `GarudaBench`, portal loads at
  `10.43.0.1:8080`, Tracking tab + mono power button work over WiFi; wired SSH still works;
  profile persists a reboot (autoconnect).

## Licences

No new dependencies (NetworkManager + raspi-config are already on the OS); project licence only.
