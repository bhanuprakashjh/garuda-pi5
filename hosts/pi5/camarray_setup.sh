#!/usr/bin/env bash
# Configure the Arducam CamArray (OV9281) for RAW V4L2 capture on Pi 5 + Trixie.
#
# WORKING recipe, proven on-bench 2026-08-01 (docs/decisions.md): the stock
# in-kernel `arducam-pivariety` driver drives the ArduChip — NO Bookworm, NO
# Arducam installer, NO libcamera swap. The CamArray is captured raw via V4L2
# while the CM3 stays on libcamera, so the portal/follow stack is untouched.
#
# Prereqs in /boot/firmware/config.txt (see docs/camarray-bringup.md):
#   camera_auto_detect=0
#   dtparam=i2c_vc=on
#   dtoverlay=arducam-pivariety,cam0     # CamArray: CSI0 + i2c_vc (bus 0), ArduChip @ 0x0c
#   dtoverlay=imx708,cam1                # keep the CM3 on the other port
# Hardware: HAT on its USB power input, SDA/SCL berg -> Pi i2c, CSI ribbon fully
#   seated on CAM1. Confirm the driver bound: `dmesg | grep pivariety` shows
#   "firmware version" + "Using sensor arducam-pivariety ... for capture".
set -euo pipefail

WIDTH=${1:-5120}      # combined frame width = 4 x 1280 (quad HAT); 2 populated -> stereo pair
HEIGHT=${2:-800}

# Locate the media device that has the pivariety sensor.
M=""
for d in /dev/media*; do
    if media-ctl -d "$d" -p 2>/dev/null | grep -q "arducam-pivariety"; then M="$d"; break; fi
done
[ -n "$M" ] || { echo "ERROR: arducam-pivariety not found — check driver/overlay/wiring." >&2; exit 1; }

# Entity name is "arducam-pivariety <bus>-<addr>", addr = 4 hex digits (e.g. 10-000c
# for i2c 0x0c). Match the hex suffix too — a [0-9-]+ regex drops the trailing 'c'
# and media-ctl then rejects the -V/-e as an unknown entity.
SENSOR=$(media-ctl -d "$M" -p 2>/dev/null | grep -oE "arducam-pivariety [0-9a-z-]+" | head -1)
NODE=$(media-ctl -d "$M" -e "rp1-cfe-csi2_ch0")
echo "media=$M sensor='$SENSOR' capture_node=$NODE"

# Raw path only: the csi2 source pad feeds BOTH the raw capture node and the ISP
# front-end (pisp-fe); the ISP branch has MUST_CONNECT pads and, left enabled
# unconfigured, makes VIDIOC_STREAMON fail (Broken pipe). Disable it, keep raw.
media-ctl -d "$M" -l "'csi2':4 -> 'pisp-fe':0 [0]" 2>/dev/null || true
media-ctl -d "$M" -l "'csi2':4 -> 'rp1-cfe-csi2_ch0':0 [1]"
media-ctl -d "$M" -V "'$SENSOR':0 [fmt:Y8_1X8/${WIDTH}x${HEIGHT}]"
v4l2-ctl -d "$NODE" --set-fmt-video=width="${WIDTH}",height="${HEIGHT}",pixelformat=GREY >/dev/null

# Lock exposure + analogue gain on the sensor subdev. Raw V4L2 has no 3A, so a
# depth/calibration rig wants these FIXED (set once, leave alone) — not drifting.
# Shared across all CamArray sensors, so L/R stay matched. Override via env:
#   GARUDA_CAM_GAIN (100..1590), GARUDA_CAM_EXPOSURE (1..65523).
SUBDEV=$(media-ctl -d "$M" -e "$SENSOR" 2>/dev/null || true)
if [ -n "$SUBDEV" ]; then
    v4l2-ctl -d "$SUBDEV" --set-ctrl=analogue_gain="${GARUDA_CAM_GAIN:-640}" 2>/dev/null || true
    v4l2-ctl -d "$SUBDEV" --set-ctrl=exposure="${GARUDA_CAM_EXPOSURE:-1500}" 2>/dev/null || true
    echo "locked exposure=${GARUDA_CAM_EXPOSURE:-1500} analogue_gain=${GARUDA_CAM_GAIN:-640} on $SUBDEV"
    # Optional frame-rate limit via vertical_blanking (bench is PASSIVELY cooled —
    # a bigger blank slows the sensor, cutting ingest/CPU/heat). Only if requested.
    if [ -n "${GARUDA_CAM_VBLANK:-}" ]; then
        v4l2-ctl -d "$SUBDEV" --set-ctrl=vertical_blanking="${GARUDA_CAM_VBLANK}" 2>/dev/null || true
        echo "set vertical_blanking=${GARUDA_CAM_VBLANK} (lower fps) on $SUBDEV"
    fi
fi

echo "CamArray ready: capture GREY ${WIDTH}x${HEIGHT} from ${NODE}"
echo "  (5120-wide = 4 tiles of 1280x800; connected cameras populate tiles 0,1 = stereo pair)"
echo "  test: sudo v4l2-ctl -d ${NODE} --stream-mmap --stream-count=5 --stream-to=/tmp/cap.raw"
echo "  services/garuda-camera: plan_split(${WIDTH}, ${HEIGHT}, 1, 4) -> use views[0], views[1]"
