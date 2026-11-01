# Skeleton only: bench dependency inventory; not a validated product recipe.
SUMMARY = "Micro XRCE-DDS Agent for PX4 simulation"
LICENSE = "Apache-2.0"
SRC_URI = "git://github.com/eProsima/Micro-XRCE-DDS-Agent.git;protocol=https;nobranch=1"
SRCREV = "73622810d984349b80bbac0ef55fc0b694d62222"
S = "${WORKDIR}/git"
# TODO: LIC_FILES_CHKSUM, packaged Fast DDS/Fast CDR/spdlog dependencies,
# disable network-fetching superbuild, target integration and licence audit.
inherit cmake
EXTRA_OECMAKE = "-DUAGENT_P2P_PROFILE=OFF -DUAGENT_CED_PROFILE=OFF"
