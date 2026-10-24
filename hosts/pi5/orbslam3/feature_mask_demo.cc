// feature_mask_demo — proves the Garuda dynamic-mask seam in ORB-SLAM3's ORBextractor.
// Extracts ORB keypoints from an image twice — once with no mask, once with the Hailo YOLO masker's
// dynamic mask — and writes side-by-side visualizations. Where the mask (red) covers the image,
// no keypoints (green) should survive.  BENCH/EVAL only (GPL-3.0, not-product-cleared).
//
//   feature_mask_demo <image.png> <mask.png|none> <out_prefix>
#include <iostream>
#include <vector>
#include <string>
#include <opencv2/opencv.hpp>
#include "ORBextractor.h"

using namespace std;

int main(int argc, char** argv)
{
    if (argc < 4) { cerr << "usage: feature_mask_demo <image> <mask|none> <out_prefix>\n"; return 2; }
    cv::Mat img = cv::imread(argv[1], cv::IMREAD_GRAYSCALE);
    if (img.empty()) { cerr << "cannot read image " << argv[1] << "\n"; return 1; }
    cv::Mat mask;
    if (string(argv[2]) != "none") {
        mask = cv::imread(argv[2], cv::IMREAD_GRAYSCALE);
        if (mask.empty()) { cerr << "cannot read mask " << argv[2] << "\n"; return 1; }
        if (mask.size() != img.size()) cv::resize(mask, mask, img.size(), 0, 0, cv::INTER_NEAREST);
    }

    // Same ORB params as Examples/Stereo/EuRoC.yaml
    ORB_SLAM3::ORBextractor ext(1200, 1.2f, 8, 20, 7);
    vector<int> lap = {0, img.cols};        // treat all as mono => all returned in _keypoints

    // operator() fills _keypoints with ALL nkeypoints (mono+stereo); the return value is only the
    // monocular subset count, so use the vectors' sizes, not the return value.
    vector<cv::KeyPoint> kp0, kp1; cv::Mat d0, d1;
    ext(img, cv::Mat(), kp0, d0, lap);   // no mask
    ext(img, mask,      kp1, d1, lap);   // with the Hailo dynamic mask

    cout << "GARUDA_KP unmasked=" << kp0.size() << " masked=" << kp1.size()
         << " dropped=" << ((int)kp0.size() - (int)kp1.size()) << endl;

    auto draw = [&](const vector<cv::KeyPoint>& kp, const cv::Mat& m) {
        cv::Mat vis; cv::cvtColor(img, vis, cv::COLOR_GRAY2BGR);
        if (!m.empty()) {
            cv::Mat overlay = vis.clone();
            overlay.setTo(cv::Scalar(40, 40, 220), m > 0);      // red-ish dynamic region
            cv::addWeighted(overlay, 0.45, vis, 0.55, 0, vis);
        }
        for (const auto& k : kp) cv::circle(vis, k.pt, 2, cv::Scalar(80, 240, 80), -1);
        return vis;
    };

    cv::imwrite(string(argv[3]) + "_unmasked.png", draw(kp0, cv::Mat()));
    cv::imwrite(string(argv[3]) + "_masked.png",   draw(kp1, mask));
    return 0;
}
