#!/usr/bin/env bash
# Build yolo26{n,m}-depth HEFs for Hailo-10H on an x86_64 Linux box (DFC never runs on the Pi).
#
# Prereqs (one-time), as run 2026-08-07 on the bench PC (Ubuntu 24.04, Python 3.11 via uv):
#   1. Download "Hailo AI SW Suite 2026-04" (.run) from the Hailo Developer Zone (login required).
#   2. Extract without installing:  sh hailo_ai_sw_suite_2026-04.run --nox11 --noexec --keep --target <dir>/suite
#      -> the DFC wheel is at suite/artifacts/hailo_dataflow_compiler-5.3.0-py3-none-linux_x86_64.whl
#   3. uv venv --python 3.11 <dir>/venv
#      uv pip install --python <dir>/venv/bin/python ultralytics <dfc-wheel>
#      (validated: ultralytics 8.4.115 + DFC 5.3.0 + TF 2.19.1 coexist)
#   4. RAM note: the exporter enables DFC Quantization-Aware Fine-Tuning, which OOM-killed a
#      14 GB box (>6.6 GB RSS, TF CPU training). With a small calibration set QAT adds nothing;
#      disable it in the venv's exporter before running:
#        sed -i 's/post_quantization_optimization(finetune, policy=enabled/post_quantization_optimization(finetune, policy=disabled/' \
#          <dir>/venv/lib/python3.11/site-packages/ultralytics/engine/exporter.py
#      Re-enable (and add swap / a GPU box) only for a production run with ~1024 calib images.
#
# LICENCE: Ultralytics YOLO26 is AGPL-3.0 — bench/eval only, not-product-cleared (see CLAUDE.md §1).
#
# Usage: ./build_yolo26_depth_hef.sh <export-root> [model] [data-yaml]
#   e.g. ... /media/.../hailo-export yolo26m-depth depth-calib.yaml
set -euo pipefail
ROOT=${1:?export root dir (holds venv/, work/)}
MODEL=${2:-yolo26n-depth}
DATA=${3:-}   # dataset yaml whose val split supplies calibration images (~1024 for production)

export TMPDIR="$ROOT/tmp" XDG_CACHE_HOME="$ROOT/.cache"
mkdir -p "$ROOT/work" "$TMPDIR"
cd "$ROOT/work"

# data=: calibration images for the DFC quantizer; default auto-downloads a tiny set (smoke only).
"$ROOT/venv/bin/yolo" export model="$MODEL.pt" format=hailo name=hailo10h ${DATA:+data="$DATA"}

echo "HEF + metadata in: $ROOT/work/${MODEL}_hailo_model/"
