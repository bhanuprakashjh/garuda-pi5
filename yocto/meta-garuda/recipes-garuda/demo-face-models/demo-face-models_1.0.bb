# Staged model-only recipe; not built or product-qualified.
SUMMARY = "Optional YuNet and SFace models for the Garuda bench demo"
LICENSE = "MIT & Apache-2.0"
LIC_FILES_CHKSUM = "file://yunet.LICENSE;md5=2bab441cad6e56796bbbd90130807551 file://sface.LICENSE;md5=3b83ef96387f14655fc854ddc3c6bd57"
ZOO_REV = "48b10fa505aeb51fbd127fc60a5c660040e823b7"
SRC_URI = "https://media.githubusercontent.com/media/opencv/opencv_zoo/${ZOO_REV}/models/face_detection_yunet/face_detection_yunet_2023mar.onnx;downloadfilename=yunet.onnx;name=yunet \
           https://media.githubusercontent.com/media/opencv/opencv_zoo/${ZOO_REV}/models/face_recognition_sface/face_recognition_sface_2021dec.onnx;downloadfilename=sface.onnx;name=sface \
           https://raw.githubusercontent.com/opencv/opencv_zoo/${ZOO_REV}/models/face_detection_yunet/LICENSE;downloadfilename=yunet.LICENSE;name=yunetlicense \
           https://raw.githubusercontent.com/opencv/opencv_zoo/${ZOO_REV}/models/face_recognition_sface/LICENSE;downloadfilename=sface.LICENSE;name=sfacelicense"
SRC_URI[yunet.sha256sum] = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
SRC_URI[sface.sha256sum] = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
SRC_URI[yunetlicense.sha256sum] = "c83b8120c50ccbd4c4f96edf53141bdd566ebb8f8e9227e415326aa1b1aba958"
SRC_URI[sfacelicense.sha256sum] = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
S = "${WORKDIR}"
inherit allarch
do_install() {
    install -d ${D}${datadir}/garuda/demo-faces
    install -m 0644 ${S}/*.onnx ${S}/*.LICENSE ${D}${datadir}/garuda/demo-faces/
}
FILES:${PN} = "${datadir}/garuda/demo-faces"
