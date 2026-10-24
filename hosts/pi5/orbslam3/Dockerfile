# ORB-SLAM3 stereo, headless, for the Garuda bench (Pi 5, aarch64).
#
# BENCH / EVALUATION ONLY (CLAUDE.md rule 6): ORB-SLAM3 is GPL-3.0. Permitted for
# bench/experimental under the 2026-08-02 relaxation; kept behind IFrontEnd, tagged
# not-product-cleared, never deep-wired into shippable services/. Clean-room mask seam
# (Task 3) is our own code; upstream ORB-SLAM3 is an external GPLv3 dependency here.
#
# WHY a container on the Pi (not bare-metal): the bench host is Trixie / GCC 14.2 / Eigen 3.4 —
# undocumented territory for ORB-SLAM3 (docs only prove GCC 11). Ubuntu 22.04 gives the
# documented-good GCC 11 + Eigen 3.4, built NATIVELY on the Pi's arm64 CPU (no cross-compile,
# no emulation). Headless: bUseViewer=false at runtime; Pangolin still links but never opens
# a window (PANGOLIN_WINDOW_URI=headless:// if ever needed).
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive TZ=UTC

# Build deps. OpenCV 4.5.4 (22.04) is closer to upstream's validated 4.4 than Trixie's 4.10.
# GL/EGL/Wayland libs satisfy Pangolin's link even though we run headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git pkg-config ca-certificates \
        libeigen3-dev libopencv-dev libboost-serialization-dev libssl-dev \
        libglew-dev libgl1-mesa-dev libglu1-mesa-dev libegl1-mesa-dev \
        libx11-dev libxkbcommon-dev libwayland-dev wayland-protocols python3-dev \
 && rm -rf /var/lib/apt/lists/*

# Pangolin v0.6 — the version pinned as known-good with ORB-SLAM3 (later Pangolin churns its API).
WORKDIR /opt
RUN git clone https://github.com/stevenlovegrove/Pangolin.git && \
    cd Pangolin && git checkout v0.6 && \
    cmake -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_STANDARD=14 \
          -DBUILD_PANGOLIN_PYTHON=OFF -DBUILD_EXAMPLES=OFF -DBUILD_TESTS=OFF \
          -DCMAKE_CXX_FLAGS="-w" && \
    cmake --build build -j2 && cmake --install build && ldconfig

# ORB-SLAM3. Patches for the GCC-11/Eigen-3.4 reality:
#   - C++11 -> C++14 across all CMakeLists (modern chrono/thread on ARM needs it)
#   - PARALLELISM (the real killer): upstream build.sh uses bare `make -j` (UNLIMITED jobs) for
#     Thirdparty and `make -j4` for the main lib. On an 8 GB Pi that explodes: many concurrent
#     cc1plus incl. Optimizer.cc (~7 GB alone at -O3) drives load to 18+ and OOM-kills the build
#     (verified: this is what killed every earlier run — NOT the file itself). A naive
#     s/make -j/make -jN/ ALSO mangles `make -j4` -> `make -jN4`. Use an anchored regex to pin
#     EVERY make line to -j2 (bounded pairing; 8 GB RAM + 8 GB swap covers Optimizer.cc + 1).
#   - CXXFLAGS=-w to silence Eigen-3.4 deprecation noise from vendored g2o
# Headless + instrumented stereo_euroc:
#   - bUseViewer false: the example hardcodes the Pangolin viewer on; headless (no display/EGL
#     in the container) it dumps core on "EGL init failed". false skips the viewer thread entirely.
#   - timing patch: print per-frame tracking time (this ORB-SLAM3 rev collects but never prints it;
#     wall-clock is useless because the example usleep()s to real-time 20 fps playback).
# Garuda patches applied to the source before the build:
#   - stereo_euroc_timing.py: per-frame tracking-time print (see header of that file)
#   - orbextractor_mask.py: the clean-room dynamic-object mask seam (Task 3) — revives the vestigial
#     _mask param so the Hailo YOLO masker's dynamic regions reject ORB keypoints.
COPY patches/stereo_euroc_timing.py /tmp/stereo_euroc_timing.py
COPY patches/orbextractor_mask.py /tmp/orbextractor_mask.py
RUN git clone https://github.com/UZ-SLAMLab/ORB_SLAM3.git && \
    cd ORB_SLAM3 && git rev-parse HEAD > /opt/ORB_SLAM3_SHA && \
    find . -name CMakeLists.txt -exec sed -i 's/++11/++14/g' {} + && \
    sed -E -i 's/make -j[0-9]*/make -j2/g' build.sh && \
    grep -n 'make -j' build.sh && \
    sed -i 's/System::STEREO, true/System::STEREO, false/' Examples/Stereo/stereo_euroc.cc && \
    python3 /tmp/stereo_euroc_timing.py && \
    python3 /tmp/orbextractor_mask.py && \
    chmod +x build.sh && CXXFLAGS="-w" ./build.sh

# feature_mask_demo — proves the mask seam (unmasked vs masked keypoints). Built against the lib.
COPY feature_mask_demo.cc /tmp/feature_mask_demo.cc
RUN cd /opt/ORB_SLAM3 && \
    g++ -std=c++14 -O2 /tmp/feature_mask_demo.cc -I include -I . -I Thirdparty/Sophus \
        $(pkg-config --cflags opencv4) -L lib -lORB_SLAM3 $(pkg-config --libs opencv4) \
        -Wl,-rpath,/opt/ORB_SLAM3/lib -o /opt/ORB_SLAM3/Examples/Stereo/feature_mask_demo

WORKDIR /opt/ORB_SLAM3
CMD ["/bin/bash"]
