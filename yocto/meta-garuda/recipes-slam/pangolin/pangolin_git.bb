# Pangolin — lightweight GL viewer / windowing, an ORB-SLAM3 build dependency.
#
# SKELETON, UNBUILT (CLAUDE.md §6). Pulled in only as a link dependency of the ORB-SLAM3 bench
# spike (hosts/pi5/orbslam3/Dockerfile pins v0.6). The bench runs HEADLESS (bUseViewer=false), so
# the viewer is never used at runtime — Pangolin is present only to satisfy the link.
#
# LICENCE: MIT — permissive, ship-eligible in principle. But its only consumer here is the
# not-product-cleared ORB-SLAM3 (see orb-slam3_git.bb), and the product-tier VIO is Basalt, which
# does not need Pangolin. So this recipe is a marker for the bench dependency graph, not a product
# component. If a product-tier tool ever needs a GL viewer, this MIT recipe can graduate.

SUMMARY = "Pangolin — portable GL viewer/windowing (ORB-SLAM3 build dependency; bench-only)"
HOMEPAGE = "https://github.com/stevenlovegrove/Pangolin"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENCE;md5=TBD-pin-at-port"

SRC_URI = "git://github.com/stevenlovegrove/Pangolin.git;protocol=https;branch=master"
SRCREV = "TBD-pin-at-port"   # bench uses tag v0.6

DEPENDS = "eigen"
# inherit cmake  # deliberately omitted — unbuilt marker recipe (see header)
