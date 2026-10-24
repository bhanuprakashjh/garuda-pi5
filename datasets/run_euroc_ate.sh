#!/usr/bin/env bash
# Run ORB-SLAM3 stereo (headless) on a EuRoC sequence and report fps + ATE vs groundtruth.
#   run_euroc_ate.sh <sequence_dir_with_mav0> <seq_name e.g. V101|MH04>
# Uses the container's stock EuRoC.yaml (all EuRoC sequences share the same stereo rig).
set -e
SEQDIR="${1:?usage: run_euroc_ate.sh <seq_dir> <seq_name>}"
SEQ="${2:?usage: run_euroc_ate.sh <seq_dir> <seq_name>}"
OUT="${HOME}/euroc_out"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"

echo "=== $SEQ: ORB-SLAM3 stereo ==="
docker run --rm -v "$SEQDIR:/data:ro" -v "$OUT:/out" garuda/orbslam3:bench bash -lc "
  cd /out && timeout 500 /opt/ORB_SLAM3/Examples/Stereo/stereo_euroc \
    /opt/ORB_SLAM3/Vocabulary/ORBvoc.txt /opt/ORB_SLAM3/Examples/Stereo/EuRoC.yaml \
    /data /opt/ORB_SLAM3/Examples/Stereo/EuRoC_TimeStamps/${SEQ}.txt ${SEQ}" 2>&1 \
  | grep -E 'GARUDA_TRACK_FPS|Map [0-9]+ has'
echo -n "=== $SEQ: "
python3 "$HERE/euroc_ate.py" "$OUT/f_${SEQ}.txt" "$SEQDIR/mav0/state_groundtruth_estimate0/data.csv"
