#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec env GARUDA_SIM=1 /usr/bin/python3 hosts/pc/lab_launcher.py "$@"
