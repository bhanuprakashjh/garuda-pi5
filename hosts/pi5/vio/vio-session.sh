#!/bin/bash
# One bounded VIO session on the Pi with the OAK-D Pro. EXPERIMENTAL, bench only.
#
#   vio-session.sh record  SECONDS NAME            raw stereo + IMU recording (for replay, calibration, scoring)
#   vio-session.sh live    SECONDS NAME [feeder options]   live odometry from the camera
#   vio-session.sh replay  SESSION_DIR NAME [feeder options]   a recording through the same solver, camera pace
#
# record and live need the camera to themselves, so the camera demo (garuda-oak-preview,
# garuda-mono) is STOPPED for the session and started again when it ends, also when it fails:
# the session runs as a transient systemd unit that owns the stop and the restart. replay
# leaves the demo alone. Nothing here talks to a flight controller.
#
# Environment:
#   GARUDA_VIO_BUILD     build directory of build_basalt.sh        (default ~/garuda-vio-build)
#   GARUDA_VIO_DATA      where sessions and runs are written       (default ~/garuda-vio)
#   GARUDA_VIO_CONFIG    oak-finer (default) or oak-shipped, or a path to a Basalt config
#   GARUDA_VIO_PYTHON    Python with DepthAI                       (default the OAK venv)
#   GARUDA_VIO_CALIB     complete Basalt calibration of THIS camera unit (optional)
#   GARUDA_VIO_STATION   station values JSON (optional)
# Without the last two the factory values are used and the summary lists what was not measured.
set -euo pipefail
umask 077
usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ $# -ge 3 ] || usage
MODE=$1; ARG=$2; NAME=$3; shift 3
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
BUILD=${GARUDA_VIO_BUILD:-$HOME/garuda-vio-build}
DATA=${GARUDA_VIO_DATA:-$HOME/garuda-vio}
PY=${GARUDA_VIO_PYTHON:-$HOME/.venvs/oak-bringup-3.12.1/bin/python}
CONFIG=${GARUDA_VIO_CONFIG:-oak-finer}
[ -f "$CONFIG" ] || CONFIG=$BUILD/config/$CONFIG.json
RUNNER=$BUILD/basalt/build/release/garuda_vio_live
case "$NAME" in *[!A-Za-z0-9._-]*|"") echo "REFUSED: NAME may contain letters, digits, . _ - only"; exit 2;; esac
mkdir -p "$DATA/sessions" "$DATA/runs"

CAL=()
[ -n "${GARUDA_VIO_CALIB:-}" ] && CAL+=(--basalt-calib "$GARUDA_VIO_CALIB")
[ -n "${GARUDA_VIO_STATION:-}" ] && CAL+=(--station "$GARUDA_VIO_STATION")
feeder() { [ -x "$RUNNER" ] && [ -f "$CONFIG" ] || { echo "REFUSED: $RUNNER or $CONFIG missing: run hosts/pi5/vio/build_basalt.sh"; exit 2; }; }

case "$MODE" in
  record)
    OUT=$DATA/sessions/$NAME
    CMD=("$PY" -u "$REPO/bench/oak_vio_record.py" --out "$OUT" --seconds "$ARG" "$@") ;;
  live)
    feeder; OUT=$DATA/runs/$NAME
    CMD=("$PY" -u "$REPO/services/garuda-vio/vio_feed.py" --camera --seconds "$ARG" --out "$OUT"
         --runner "$RUNNER" --config "$CONFIG" "${CAL[@]}" "$@") ;;
  replay)
    feeder; OUT=$DATA/runs/$NAME
    [ -f "$ARG/frames.jsonl" ] || { echo "REFUSED: $ARG is not a recorded session"; exit 2; }
    # no camera: runs in this shell, the demo keeps running
    exec env PYTHONPATH=/usr/lib/python3/dist-packages "$PY" -u "$REPO/services/garuda-vio/vio_feed.py" \
         --replay "$ARG" --realtime --out "$OUT" --runner "$RUNNER" --config "$CONFIG" "${CAL[@]}" "$@" ;;
  *) usage ;;
esac
[ -e "$OUT" ] && { echo "REFUSED: $OUT exists; choose another NAME"; exit 2; }

# Which demo services are running now: only those are stopped, and only those are started again.
RUNNING=""
read -r -a camera_services <<< "${OAK_CAMERA_SERVICES:-garuda-oak-preview garuda-mono}"
for u in "${camera_services[@]}"; do
  [[ "$u" =~ ^[a-zA-Z0-9@_.-]+$ && "$u" != -* ]] || { echo "Invalid camera service name: $u"; exit 2; }
  systemctl is-active --quiet "$u" && RUNNING="$RUNNING $u"
done
UNIT=garuda-vio-session
sudo systemctl reset-failed "$UNIT" 2>/dev/null || true
PROPS=()
if [ -n "$RUNNING" ]; then
  PROPS+=(-p "ExecStartPre=+/bin/systemctl stop$RUNNING" -p "ExecStopPost=+/bin/systemctl start$RUNNING")
  echo "camera demo paused for this session:$RUNNING"
fi
# The OAK venv has no OpenCV of its own; the system one is used.
IO=--pipe; [ -t 1 ] && IO=--pty                       # show the session's own output either way
sudo systemd-run --unit="$UNIT" --uid="$(id -u)" --collect --wait $IO \
     -p UMask=0077 --setenv=PYTHONPATH=/usr/lib/python3/dist-packages "${PROPS[@]}" "${CMD[@]}"
echo "written: $OUT"
[ -z "$RUNNING" ] || echo "camera demo restarted:$RUNNING"
