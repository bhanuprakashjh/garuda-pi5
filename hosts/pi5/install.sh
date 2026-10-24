#!/usr/bin/env bash
# Garuda SDK bench provisioning — Pi 5 + Hailo-10H (AI HAT+ 2).
# Run ON the Pi as the bench user. Idempotent: safe to re-run (CLAUDE.md rule 3).
# Mirrors what was actually run on 2026-07-29 + the 2026-07-30 portal deploy
# (see bench/versions.md); update both together.
#
# Assumes: Raspberry Pi OS Trixie 64-bit Lite, first-boot done with `ssh` +
# `userconf.txt` seeded on bootfs (user creation + SSH enablement happen there,
# not here).
set -euo pipefail

GENAI_DEB_URL="https://dev-public.hailo.ai/2025_12/Hailo10/hailo_gen_ai_model_zoo_5.1.1_arm64.deb"

# --deploy-only re-runs just the code-deploy step [5/5] (rsync services + restart
# units), skipping OS/Hailo provisioning [1..4]. Use it to push a code change to
# an already-provisioned bench without an apt round-trip (CLAUDE.md rule 11).
DEPLOY_ONLY=0
if [ "${1:-}" = "--deploy-only" ]; then
    DEPLOY_ONLY=1
    echo "== --deploy-only: skipping OS/Hailo provisioning [1..4], running deploy [5] only"
fi

if [ "$DEPLOY_ONLY" -eq 0 ]; then
echo "== [1/5] Trixie check"
. /etc/os-release
if [ "${VERSION_CODENAME:-}" != "trixie" ]; then
    echo "ERROR: expected Raspberry Pi OS Trixie, found '${VERSION_CODENAME:-unknown}'." >&2
    echo "h10 packages exist only in the Trixie repo (docs/decisions.md 2026-07-29)." >&2
    exit 1
fi

echo "== [2/5] Passwordless sudo for bench user (Trixie stopped granting it)"
SUDOERS_FILE=/etc/sudoers.d/010-${USER}-nopasswd
if ! sudo test -f "$SUDOERS_FILE"; then
    echo "${USER} ALL=(ALL) NOPASSWD: ALL" | sudo tee "$SUDOERS_FILE" >/dev/null
    sudo chmod 440 "$SUDOERS_FILE"
    echo "   created $SUDOERS_FILE"
else
    echo "   already present"
fi

echo "== [3/5] Hailo-10H stack + ethtool + python3-opencv (h10 family ONLY — never plain hailort/hailo-all)"
sudo apt update
# python3-opencv (BSD) is garuda-portal's calibration.py dependency (cv2, lazy-imported).
sudo DEBIAN_FRONTEND=noninteractive apt install -y hailo-h10-all ethtool python3-opencv
# Deliberately NO `apt upgrade` here (CLAUDE.md rule 11).

echo "== [4/5] Hailo GenAI model zoo (provides hailo-ollama; bench tool, not a service dep)"
if ! command -v hailo-ollama >/dev/null; then
    tmp=$(mktemp -d)
    wget -q -O "$tmp/genai.deb" "$GENAI_DEB_URL"
    sudo dpkg -i "$tmp/genai.deb"
    rm -rf "$tmp"
else
    echo "   already installed: $(command -v hailo-ollama)"
fi
fi  # end provisioning [1..4] (skipped under --deploy-only)

echo "== [5/5] garuda-portal + garuda-configd deploy (replaces bench garuda-cam-portal on :8080)"
DEPLOY_SRC="$HOME/garuda-sdk-deploy"
for d in services/garuda-portal services/garuda-configd services/garuda-inference \
         services/garuda-follow interfaces hosts/pi5/units; do
    if [ ! -d "$DEPLOY_SRC/$d" ]; then
        echo "ERROR: $DEPLOY_SRC/$d not found — rsync the repo (services/, interfaces/, hosts/) to $DEPLOY_SRC first." >&2
        exit 1
    fi
done

sudo mkdir -p /opt/garuda
sudo chown "${USER}:${USER}" /opt/garuda

# garuda-configd may be root:root-locked from a previous run of the F2 hardening
# below; hand it back to the deploy user first so the unprivileged rsync below
# can still write into it (check-then-modify, CLAUDE.md rule 3 idempotency).
if [ -d /opt/garuda/garuda-configd ]; then
    sudo chown -R "${USER}:${USER}" /opt/garuda/garuda-configd
fi

# --delete converges deletions too (removed files on the source disappear on the Pi);
# tests/__pycache__/.pytest_cache are dev-tree cruft, not part of the running service.
rsync -a --delete --exclude='__pycache__' --exclude='.pytest_cache' --exclude='tests' \
    "$DEPLOY_SRC/services/garuda-portal/"  /opt/garuda/garuda-portal/
rsync -a --delete --exclude='__pycache__' --exclude='.pytest_cache' --exclude='tests' \
    "$DEPLOY_SRC/services/garuda-configd/" /opt/garuda/garuda-configd/
rsync -a --delete --exclude='__pycache__' \
    "$DEPLOY_SRC/services/garuda-inference/" /opt/garuda/garuda-inference/

# garuda-portal's follow pipeline (follow_pipeline.py) imports the pure follow
# modules + garuda_interfaces from sibling dirs. The deploy flattens them to
# /opt/garuda/garuda-follow and /opt/garuda/interfaces — exactly the deploy-layout
# location follow_pipeline.py probes (parents[1]). Both run User=garuda like the
# portal, so no root-locking (unlike garuda-configd below).
rsync -a --delete --exclude='__pycache__' --exclude='.pytest_cache' --exclude='tests' \
    "$DEPLOY_SRC/services/garuda-follow/" /opt/garuda/garuda-follow/
rsync -a --delete --exclude='__pycache__' --exclude='.pytest_cache' --exclude='tests' \
    "$DEPLOY_SRC/interfaces/" /opt/garuda/interfaces/

# garuda-configd.service has no User= (runs as root); the rsync above just wrote
# its tree as the unprivileged deploy user, which means the unprivileged
# account could edit code that root then executes on next restart — a
# trust-boundary break. Lock the tree to root, strip group/other write.
# garuda-portal/garuda-inference stay deploy-user-owned: their units run
# User=garuda (whole-branch review 2026-07-30, F2).
sudo chown -R root:root /opt/garuda/garuda-configd
sudo chmod -R go-w /opt/garuda/garuda-configd

# garuda_inference.py hardcodes sys.path.insert(0, "/home/garuda/garuda_pylib") to find
# garuda_interfaces — that tree was bench-deployed separately in earlier bring-up.
# Verify it's there rather than laying down a second copy under /opt/garuda.
if [ ! -d "$HOME/garuda_pylib/garuda_interfaces" ]; then
    echo "WARN: $HOME/garuda_pylib/garuda_interfaces missing — garuda-inference start will fail until it's deployed there." >&2
fi

sudo install -m644 "$DEPLOY_SRC/hosts/pi5/units/garuda-configd.service" /etc/systemd/system/garuda-configd.service
sudo install -m644 "$DEPLOY_SRC/hosts/pi5/units/garuda-portal.service"  /etc/systemd/system/garuda-portal.service
# garuda-depth: CamArray stereo-depth explorer (:8097), embedded in the portal Depth tab. Runs
# from the deploy tree (its ExecStart path); idles when no viewer. NPU models are EVAL-ONLY.
sudo install -m644 "$DEPLOY_SRC/hosts/pi5/units/garuda-depth.service"   /etc/systemd/system/garuda-depth.service
sudo systemctl daemon-reload

echo "   stopping/disabling transient bench unit garuda-cam-portal (same camera, same :8080)"
sudo systemctl stop garuda-cam-portal 2>/dev/null || true
sudo systemctl disable garuda-cam-portal 2>/dev/null || true

# enable for boot, then RESTART (not `enable --now`) so a redeploy actually loads
# the new code: `enable --now` no-ops on an already-active unit, leaving the old
# process running. (Bench finding 2026-07-31: the follow overlay + follow_pipeline
# rsynced fine but the live portal kept serving pre-follow code — the follow route
# 404'd — until an explicit restart.) `restart` also starts an inactive unit, so
# this is correct for first install and redeploy alike.
sudo systemctl enable garuda-configd.service garuda-portal.service garuda-depth.service
sudo systemctl restart garuda-configd.service garuda-portal.service garuda-depth.service

echo "== sanity: firmware identify (must report HAILO10H)"
hailortcli fw-control identify

echo "== sanity: throttling flags (0x0 = OK; anything else -> log in benchlog, distrust numbers)"
vcgencmd get_throttled

echo "DONE. garuda-portal should be reachable at http://<pi>:8080/ — record versions in"
echo "bench/versions.md and this run in bench/benchlog.md if anything changed."
