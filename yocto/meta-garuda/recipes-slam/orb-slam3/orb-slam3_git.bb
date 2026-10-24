# ORB-SLAM3 — stereo VIO front end (BENCH / EVALUATION ONLY).
#
# SKELETON, UNBUILT (CLAUDE.md §6). Landed on the bench 2026-08-02 as a hybrid-SLAM spike:
# built in an aarch64 Ubuntu-22.04/GCC-11 container on the Pi (hosts/pi5/orbslam3/), benchmarked
# on EuRoC MH_01 at ~22.8 fps stereo tracking (bench/benchlog.md 2026-08-02). Wrapped behind
# IFrontEnd for the ORB-SLAM3 + Hailo-YOLO dynamic-masking evaluation (docs/superpowers/plans/
# 2026-08-02-hybrid-orbslam3-yolo-masking.md).
#
# *** LICENCE: GPL-3.0-only — NOT PRODUCT-CLEARED. *** Permitted for bench/experimental under the
# 2026-08-02 owner relaxation (docs/decisions.md), kept behind the IFrontEnd plugin boundary and
# tagged not-product-cleared. This recipe is a MARKER, not a shippable component: the Hailo-15
# product BSP must NOT ship ORB-SLAM3. The product-tier VIO is the licence-clean **Basalt** (BSD),
# already chosen in docs/decisions.md (2026-07-30). If any learned/dynamic-masking capability
# proven here is wanted at product tier, it must be re-implemented clean-room on Basalt or a
# permissively-licensed stack — never by shipping this GPLv3 code.

SUMMARY = "ORB-SLAM3 stereo/visual-inertial SLAM (bench evaluation only — GPLv3, not product-cleared)"
HOMEPAGE = "https://github.com/UZ-SLAMLab/ORB_SLAM3"
LICENSE = "GPL-3.0-only"
LIC_FILES_CHKSUM = "file://LICENSE;md5=TBD-pin-at-port"

# SRC_URI is a stub. If this were ever built (it must not ship), pin SRCREV to the evaluated commit.
SRC_URI = "git://github.com/UZ-SLAMLab/ORB_SLAM3.git;protocol=https;branch=master"
SRCREV = "4452a3c4ab75b1cde34e5505a36ec3f9edcdc4c4"   # the commit benchmarked on the bench 2026-08-02

DEPENDS = "pangolin opencv eigen boost"

# Deliberately NOT inheriting cmake/adding do_compile: this layer entry exists to RECORD the
# bench evaluation and its licence status, not to build a product artifact.
PNBLACKLIST[orb-slam3] = "GPL-3.0 not product-cleared — bench evaluation only (CLAUDE.md §6)"
