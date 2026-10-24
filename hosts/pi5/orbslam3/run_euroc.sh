#!/usr/bin/env bash
# Run ORB-SLAM3 stereo on a EuRoC sequence, HEADLESS, and report timing.
# The image's stereo_euroc must be built with bUseViewer=false (no Pangolin/EGL) — the Dockerfile
# bakes that; an older image can be patched+committed once (see decisions.md 2026-08-02).
#
#   run_euroc.sh <sequence_dir_containing_mav0> <seq_name e.g. MH01>
#
# Prints ORB-SLAM3's median/mean per-frame tracking time (seconds) + wall seconds. fps = 1/mean.
# BENCH/EVAL only — ORB-SLAM3 GPL-3.0, not-product-cleared.
set -euo pipefail
DATA="${1:?usage: run_euroc.sh <sequence_dir_with_mav0> <seq_name>}"
SEQ="${2:?usage: run_euroc.sh <sequence_dir_with_mav0> <seq_name>}"
IMAGE=garuda/orbslam3:bench
docker run --rm -v "$DATA:/data:ro" "$IMAGE" bash -lc "
  cd /opt/ORB_SLAM3 &&
  S=\$(date +%s.%N) &&
  ./Examples/Stereo/stereo_euroc Vocabulary/ORBvoc.txt Examples/Stereo/EuRoC.yaml \
      /data Examples/Stereo/EuRoC_TimeStamps/${SEQ}.txt ${SEQ}_run ;
  RC=\$? ; E=\$(date +%s.%N) ;
  awk \"BEGIN{printf \\\"WALL_SECONDS=%.1f\\n\\\", \$E-\$S}\" ;
  exit \$RC
"
