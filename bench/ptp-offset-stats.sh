#!/usr/bin/env bash
# Capture ptp4l "master offset" samples for N seconds and report stats (ns).
# Runs ON the bench Pi against the garuda-ptp4l-client transient unit (or any ptp4l unit).
# Usage: sudo ./ptp-offset-stats.sh [duration_s] [unit]
# Note: ptp4l -m double-logs (stdout + syslog) into journald; we keep only the
# syslog-tagged lines ("ptp4l[...") so each sample counts once.
set -euo pipefail

DUR=${1:-300}
UNIT=${2:-garuda-ptp4l-client}

timeout --foreground "$DUR" journalctl -f -u "$UNIT" --no-pager -o cat 2>/dev/null | \
grep --line-buffered '^ptp4l\[' | \
awk '
/master offset/ {
    for (i = 1; i <= NF; i++) if ($i == "offset") { o = $(i+1); break }
    if (o != "") {
        n++; s += o; ss += o * o
        if (n == 1 || o < min) min = o
        if (n == 1 || o > max) max = o
        o = ""
    }
}
END {
    if (n == 0) { print "no samples captured"; exit 1 }
    printf "samples=%d  mean=%.1f ns  rms=%.1f ns  min=%d ns  max=%d ns\n", \
        n, s/n, sqrt(ss/n), min, max
}' || true
