#!/usr/bin/env bash
# CM3 (IMX708) delivered-frame metrics per sensor mode, from rpicam-vid PTS traces.
# Runs ON the bench Pi. Raw YUV420 to /dev/null — deliberately NO video encode
# (Pi 5 has no hardware encoder; software encode would poison the numbers).
# Drop heuristic: inter-frame gap > 1.5x nominal frame time.
# Usage: ./cm3-stream-metrics.sh [duration_ms per mode, default 30000]
set -euo pipefail

DUR_MS=${1:-30000}

echo "throttled before: $(vcgencmd get_throttled)"

run_mode () {
    local W=$1 H=$2 FPS=$3
    local pts="/tmp/pts_${W}x${H}_${FPS}.txt"
    rpicam-vid -n -t "$DUR_MS" --width "$W" --height "$H" --framerate "$FPS" \
        --mode "${W}:${H}:10:P" --codec yuv420 -o /dev/null --save-pts "$pts" \
        >/dev/null 2>&1
    awk -v fps="$FPS" -v mode="${W}x${H}@${FPS}" '
        /^#/ { next }                         # skip "# timecode format v2" header
        !init { prev = $1; first = $1; init = 1; n = 1; next }
        {
            d = $1 - prev                     # PTS delta in ms
            if (d > maxd) maxd = d
            if (d > 1.5 * 1000 / fps) drops++
            prev = $1; n++
        }
        END {
            if (n < 2) { printf "%s: insufficient frames (%d)\n", mode, n; exit 1 }
            dur = (prev - first) / 1000.0
            printf "%s: frames=%d dur=%.2fs delivered_fps=%.2f max_gap=%.2fms drops(gap>1.5T)=%d\n",
                   mode, n, dur, (n - 1) / dur, maxd, drops + 0
        }' "$pts"
}

run_mode 1536 864 120
run_mode 2304 1296 56
run_mode 4608 2592 14

echo "throttled after: $(vcgencmd get_throttled)"
