# arducam-pivariety — Arducam Pivariety / CamArray (OV9281 quad) V4L2 sensor-bridge driver.
#
# SKELETON, UNBUILT (CLAUDE.md §6 rule 6). The CamArray capture path landed on the bench
# 2026-08-01: hosts/pi5/camarray_backend.py (raw-V4L2 ICaptureBackend) + camarray_setup.sh.
# On the Pi 5 bench this driver ships IN-TREE with the Raspberry Pi downstream kernel
# (dtoverlay=arducam-pivariety,cam0 binds the ArduChip @ i2c 0x0c) — no recipe is built here.
# At the Hailo-15 product tier the driver must be ported into the Hailo OS BSP kernel; pin
# SRCREV + LIC_FILES_CHKSUM then. See docs/camarray-bringup.md, docs/decisions.md (2026-08-01).
#
# LICENCE: Linux kernel module, GPL-2.0-only. GPLv2 kernel drivers are allowed as OS
# components (CLAUDE.md §6) — NOT GPLv3, and NOT linked into any Garuda service. The userland
# the backend shells out to (v4l2-ctl / media-ctl from v4l-utils, GPL-2.0/LGPL-2.1) is stock
# oe-core — no Garuda recipe needed, same rationale as linuxptp running as an external daemon.

SUMMARY = "Arducam Pivariety / CamArray (OV9281 quad) V4L2 sensor-bridge kernel driver"
HOMEPAGE = "https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver"
LICENSE = "GPL-2.0-only"
LIC_FILES_CHKSUM = "file://LICENSE;md5=TBD-pin-at-port"

# SRC_URI is a stub — pin to the kernel tree / out-of-tree module source Hailo OS builds.
SRC_URI = "git://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver.git;protocol=https;branch=master"
SRCREV = "TBD-pin-at-port"

inherit module

# Not built on the bench (meta-garuda is product-tier only, Phase 9).
