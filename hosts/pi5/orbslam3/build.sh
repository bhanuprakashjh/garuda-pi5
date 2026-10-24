#!/usr/bin/env bash
# Build / run the ORB-SLAM3 stereo bench container. Idempotent: docker layer cache means a
# re-run with an unchanged Dockerfile is a near-instant no-op.
#
#   build.sh build                     # build the image (long; cached on re-run)
#   build.sh shell                     # interactive shell in the image
#   build.sh euroc <dataset_dir> <seq> # run stereo_euroc headless on a EuRoC sequence
#
# BENCH/EVAL only — ORB-SLAM3 is GPL-3.0, not-product-cleared (see Dockerfile header).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE=garuda/orbslam3:bench

cmd="${1:-build}"
case "$cmd" in
  build)
    docker build -t "$IMAGE" "$HERE"
    echo "built $IMAGE (ORB-SLAM3 SHA: $(docker run --rm "$IMAGE" cat /opt/ORB_SLAM3_SHA))"
    ;;
  shell)
    docker run --rm -it "$IMAGE"
    ;;
  euroc)
    # $2 = host dir holding the EuRoC sequence (mav0/...), $3 = timestamps file basename
    DATA="${2:?usage: build.sh euroc <dataset_dir> <seq_name>}"
    SEQ="${3:?usage: build.sh euroc <dataset_dir> <seq_name>}"
    docker run --rm -v "$DATA:/data:ro" "$IMAGE" bash -lc "
      cd /opt/ORB_SLAM3 &&
      ./Examples/Stereo/stereo_euroc \
        Vocabulary/ORBvoc.txt \
        Examples/Stereo/EuRoC.yaml \
        /data \
        Examples/Stereo/EuRoC_TimeStamps/${SEQ}.txt \
        dataset-${SEQ}_stereo"
    ;;
  *)
    echo "unknown command: $cmd" >&2; exit 2 ;;
esac
