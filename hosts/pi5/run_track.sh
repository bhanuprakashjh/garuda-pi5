#!/usr/bin/env bash
# Run garuda-track live: free the single-tenant camera + NPU, then launch the service.
# Idempotent — safe to re-run; only stops services that are actually active. Args pass through
# to track_service.py (e.g. --view-port 0 to disable the overlay server).
set -u
DIR=/home/garuda/garuda-sdk-deploy/services/garuda-track

# The CM3 + Hailo NPU are single-tenant: garuda-depth (OV9281+NPU) and the portal's camera stream
# both hold hardware garuda-track needs. Stop only what's running.
for svc in garuda-depth garuda-portal; do
    if systemctl is-active --quiet "$svc"; then
        echo "[run_track] stopping $svc" >&2
        sudo systemctl stop "$svc"
    fi
done
pkill -f rpicam-vid 2>/dev/null || true
pkill -9 -f track_service 2>/dev/null || true
sleep 2
rm -f /dev/shm/garuda_track.ipc /tmp/garuda_track.cmd

echo "[run_track] launching track_service $*" >&2
cd "$DIR" || exit 1
exec python3 track_service.py "$@"
