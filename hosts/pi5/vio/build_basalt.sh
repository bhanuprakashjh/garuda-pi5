#!/bin/bash
# Build Basalt and the Garuda pipe-fed live runner on the Pi 5. EXPERIMENTAL, bench only.
#
#   hosts/pi5/vio/build_basalt.sh [BUILD_DIR]        default BUILD_DIR: ~/garuda-vio-build
#
# Safe to re-run: every step checks before it changes anything. Nothing is installed on the
# system; the build stays under BUILD_DIR. Missing packages are LISTED, not installed
# (installing them is a system change for the operator to approve).
#
# Basalt: BSD-3-Clause, https://gitlab.com/VladyslavUsenko/basalt  (pinned commit below).
# Its dependencies are built by its own vcpkg manifest; third-party licences inside that tree
# have not been audited (docs/dependencies/2026-11-12-oak-vio-licence-review.md, item 4).
# First build on a Pi 5 took about 75 minutes with -j2 (bench/benchlog.md 2026-11-19); the Pi
# gets hot, so give it airflow.
set -euo pipefail

BASALT_URL=https://gitlab.com/VladyslavUsenko/basalt.git
BASALT_COMMIT=0f3b2b52c807f70ff4e2973ce253c73329eea7bc     # release 0.1.7
BUILD=$(realpath -m -- "${1:-$HOME/garuda-vio-build}")
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
LIVE_SRC=$REPO/services/garuda-vio/basalt_live.cpp
# The set present on the bench Pi when the build was done (bench/versions.md 2026-11-19).
PACKAGES="build-essential ccache cmake curl git libboost-date-time-dev libboost-filesystem-dev
 libboost-program-options-dev libboost-regex-dev libboost-serialization-dev libbz2-dev
 libeigen3-dev libfmt-dev libglew-dev libgtest-dev libjpeg-dev liblz4-dev libopencv-dev
 libpng-dev libssl-dev libtbb-dev libwayland-dev libx11-dev libxkbcommon-dev ninja-build
 pkg-config tar unzip wayland-protocols zip"

[ -f "$LIVE_SRC" ] || { echo "REFUSED: $LIVE_SRC not found (run from a Garuda checkout)"; exit 2; }
missing=""
for p in $PACKAGES; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing="$missing $p"
done
if [ -n "$missing" ]; then
  echo "REFUSED: build packages are missing. This script does not install them. Review, then run:"
  echo "  sudo apt-get install --no-install-recommends$missing"
  echo "(an install, not an upgrade; record what changes in bench/versions.md)"
  exit 3
fi

mkdir -p "$BUILD"
cd "$BUILD"
if [ ! -d basalt/.git ]; then
  git clone "$BASALT_URL" basalt
fi
cd basalt
if [ "$(git rev-parse HEAD)" != "$BASALT_COMMIT" ]; then
  git fetch origin "$BASALT_COMMIT" 2>/dev/null || git fetch origin
  # Preserve edits in an existing checkout.
  git diff --quiet && git diff --cached --quiet || { echo "Changed Basalt checkout. Choose a fresh build directory."; exit 2; }
  git checkout "$BASALT_COMMIT"
fi
git submodule update --init --recursive

# 1. RealSense support is optional and its libusb port does not build here: leave it out.
python3 - <<'PY'
import json
p = "vcpkg.json"
m = json.load(open(p))
before = json.dumps(m, sort_keys=True)
m["dependencies"] = [d for d in m["dependencies"] if (d.get("name") if isinstance(d, dict) else d) != "realsense2"]
m["overrides"] = [o for o in m.get("overrides", []) if o.get("name") != "realsense2"]
if not m["overrides"]:
    m.pop("overrides")
if json.dumps(m, sort_keys=True) != before:
    json.dump(m, open(p, "w"), indent=2)
    print("vcpkg.json: realsense2 removed")
PY
# 2. GCC 14 reports an unused variable in dataset_io_euroc.h; upstream builds with -Werror.
if grep -q -- '-Wall -Wextra -Werror ' CMakeLists.txt; then
  sed -i 's/-Wall -Wextra -Werror /-Wall -Wextra /' CMakeLists.txt
  echo "CMakeLists.txt: -Werror removed"
fi
# 3. The live runner is one extra target; its source stays in the Garuda repository.
if ! grep -q GARUDA_VIO_LIVE_SRC CMakeLists.txt; then
  cat >> CMakeLists.txt <<'CM'

# garuda: pipe-fed live runner (source lives in the Garuda repository)
if(GARUDA_VIO_LIVE_SRC)
  add_executable(garuda_vio_live ${GARUDA_VIO_LIVE_SRC})
  target_link_libraries(garuda_vio_live basalt ${BASALT_PANGO_TARGETS} basalt::cli11)
endif()
CM
  echo "CMakeLists.txt: garuda_vio_live target added"
fi

export VCPKG_FORCE_SYSTEM_BINARIES=1 VCPKG_MAX_CONCURRENCY=2 CMAKE_BUILD_PARALLEL_LEVEL=2
[ -x thirdparty/vcpkg/vcpkg ] || ./thirdparty/vcpkg/bootstrap-vcpkg.sh -disableMetrics
cmake --preset release -DGARUDA_VIO_LIVE_SRC="$LIVE_SRC"
cmake --build --preset release -j2 --target basalt_vio garuda_vio_live

# Solver settings: Basalt's own EuRoC settings, and the finer-grid candidate from the bench
# sweep (docs/VIO_EXPERIMENT.md). Generated here so no third-party file is copied into the repo.
mkdir -p "$BUILD/config"
python3 - "$BUILD" <<'PY'
import json, sys
build = sys.argv[1]
base = json.load(open(build + "/basalt/data/euroc_config.json"))
json.dump(base, open(build + "/config/oak-shipped.json", "w"), indent=1)
base["value0"]["config.optical_flow_detection_grid_size"] = 35
base["value0"]["config.optical_flow_levels"] = 4
json.dump(base, open(build + "/config/oak-finer.json", "w"), indent=1)
PY
ls -l "$BUILD/basalt/build/release/garuda_vio_live" "$BUILD/basalt/build/release/basalt_vio" "$BUILD/config/"
echo "BUILD_OK $BUILD"
