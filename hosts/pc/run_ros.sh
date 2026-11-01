#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ "${GARUDA_SIM:-}" != 1 ]]; then echo 'GARUDA_SIM=1 required' >&2; exit 1; fi
set +u
source .runtime/matched_ws/install/setup.bash
set -u
export ROS_DOMAIN_ID=42 ROS_LOCALHOST_ONLY=1 RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
exec python services/garuda-follow/sitl_demo.py "$@"
