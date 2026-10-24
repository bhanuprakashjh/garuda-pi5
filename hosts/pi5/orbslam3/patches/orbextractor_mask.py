# Clean-room dynamic-object mask seam for ORB-SLAM3's ORBextractor.
#
# Provenance: reimplemented from the MECHANISM described in the dynamic-SLAM research (DynaSLAM's
# post-extraction keypoint reject + YOLO_ORB_SLAM3's extractor-level seam), NOT copied from any
# GPLv3 repo. It revives ORB-SLAM3's own vestigial `_mask` parameter (declared in operator() but
# never referenced anywhere in ORBextractor.cc) and makes it functional: keypoints whose ORIGINAL-
# resolution position falls inside a dynamic region are dropped before orientation/descriptors.
#
# Mask convention: single-channel uint8 at the ORIGINAL image resolution; pixel > 0 == DYNAMIC ==
# reject (matches services/garuda-mask build_dynamic_mask). Coordinate map: a level-L keypoint at
# pt maps to original pixel pt * mvScaleFactor[L] (level 0 == original image).
h = "include/ORBextractor.h"
cc = "src/ORBextractor.cc"
sh = open(h).read()
sc = open(cc).read()
assert "mDynMask" not in sh and "mDynMask" not in sc, "already patched"

# (1) .h — add the mask member next to the (public) pyramid member
sh = sh.replace(
    "    std::vector<cv::Mat> mvImagePyramid;",
    "    std::vector<cv::Mat> mvImagePyramid;\n"
    "    cv::Mat mDynMask;   // Garuda: dynamic-object reject mask (orig-res, >0 => drop keypoint)",
    1)
assert "mDynMask" in sh, "h anchor missed"
open(h, "w").write(sh)

# (2) .cc operator() — capture the (previously dead) _mask param
sc = sc.replace(
    "        Mat image = _image.getMat();\n        assert(image.type() == CV_8UC1 );",
    "        Mat image = _image.getMat();\n        assert(image.type() == CV_8UC1 );\n"
    "        // Garuda: revive the vestigial _mask param -> feed the dynamic mask to the octree filter\n"
    "        mDynMask = _mask.empty() ? cv::Mat() : _mask.getMat();",
    1)

# (3) .cc ComputeKeyPointsOctTree — per-level scale-correct keypoint reject, before computeOrientation
anchor = "                keypoints[i].size = scaledPatchSize;\n            }\n"
filt = (
    "                keypoints[i].size = scaledPatchSize;\n"
    "            }\n"
    "\n"
    "            // ---- Garuda: dynamic-object mask filter (clean-room). Drop level-L keypoints whose\n"
    "            // ORIGINAL-resolution position (pt * scaleFactor[L]) is inside a dynamic region the\n"
    "            // Hailo YOLO masker flagged. Before orientation/descriptors => masked points cost 0. ----\n"
    "            if(!mDynMask.empty())\n"
    "            {\n"
    "                const float sf = mvScaleFactor[level];\n"
    "                std::vector<cv::KeyPoint> kept; kept.reserve(keypoints.size());\n"
    "                for(size_t ki=0; ki<keypoints.size(); ki++)\n"
    "                {\n"
    "                    const int ox = cvRound(keypoints[ki].pt.x * sf);\n"
    "                    const int oy = cvRound(keypoints[ki].pt.y * sf);\n"
    "                    if(ox>=0 && oy>=0 && ox<mDynMask.cols && oy<mDynMask.rows &&\n"
    "                       mDynMask.at<uchar>(oy,ox) > 0)\n"
    "                        continue;   // inside dynamic region -> reject\n"
    "                    kept.push_back(keypoints[ki]);\n"
    "                }\n"
    "                keypoints.swap(kept);\n"
    "            }\n"
)
assert sc.count(anchor) >= 1, "cc keypoint anchor not found"
sc = sc.replace(anchor, filt, 1)   # first occurrence = ComputeKeyPointsOctTree (ComputeKeyPointsOld is unused)
assert "mDynMask =" in sc and "dynamic region -> reject" in sc, "cc patch incomplete"
open(cc, "w").write(sc)
print("patched ORBextractor.h + .cc: revived _mask param + per-level dynamic-mask keypoint filter")
