"""StereoRig — load a stereo calibration YAML and rectify + match a pair.

Host-agnostic (OpenCV only). Reads the file written by calib/stereo_calibrate.py
(K/D/R/P per camera + Q + baseline), precomputes the rectification maps once, and
provides rectify() and a StereoSGBM disparity(). Feeds the depth viewer and, later,
StereoDepthSource. Disparity->depth stays Z = f*B/d (see stereo.disparity_to_depth_mm).
"""
from __future__ import annotations

import pathlib

import cv2
import numpy as np


class StereoRig:
    def __init__(self, yaml_path):
        fs = cv2.FileStorage(str(yaml_path), cv2.FILE_STORAGE_READ)
        if not fs.isOpened():
            raise FileNotFoundError(f"cannot open calibration {yaml_path}")
        self.width = int(fs.getNode("image_width").real())
        self.height = int(fs.getNode("image_height").real())
        K1, D1 = fs.getNode("K1").mat(), fs.getNode("D1").mat()
        K2, D2 = fs.getNode("K2").mat(), fs.getNode("D2").mat()
        R1, P1 = fs.getNode("R1").mat(), fs.getNode("P1").mat()
        R2, P2 = fs.getNode("R2").mat(), fs.getNode("P2").mat()
        self.Q = fs.getNode("Q").mat()
        self.baseline_mm = float(fs.getNode("baseline_mm").real())
        self.focal_px = float(P1[0, 0])       # rectified focal (P1 fx)
        self.rms_stereo = float(fs.getNode("rms_stereo").real())
        # Disparity convention: a standard left-reference rig has P2[0,3] = Tx*f < 0.
        # If it's POSITIVE, cam1 (tile0) is the physically-RIGHT camera, so cam2 must be
        # the SGBM reference or every disparity comes out negative and gets masked away
        # (verified on-bench 2026-08-01: T[0]=+40.7mm -> tile0 is the right camera).
        self.reference_is_cam2 = float(P2[0, 3]) > 0
        fs.release()

        size = (self.width, self.height)
        self.map1x, self.map1y = cv2.initUndistortRectifyMap(K1, D1, R1, P1, size, cv2.CV_16SC2)
        self.map2x, self.map2y = cv2.initUndistortRectifyMap(K2, D2, R2, P2, size, cv2.CV_16SC2)
        self._sgbm = None

    def rectify(self, left, right):
        rl = cv2.remap(left, self.map1x, self.map1y, cv2.INTER_LINEAR)
        rr = cv2.remap(right, self.map2x, self.map2y, cv2.INTER_LINEAR)
        return rl, rr

    def order_for_matching(self, rect_cam1, rect_cam2):
        """Return (left_reference, right) so StereoSGBM yields POSITIVE disparity for
        real objects. Uses the calibration sign (reference_is_cam2). The returned first
        element is the frame the disparity map is aligned to (display it as 'left')."""
        if self.reference_is_cam2:
            return rect_cam2, rect_cam1
        return rect_cam1, rect_cam2

    def _make_sgbm(self, num_disp: int, block: int):
        # Looser than defaults: rough calibration leaves ~1-3px vertical residual, so
        # a bigger block + lower uniqueness recovers a lot more valid pixels.
        return cv2.StereoSGBM_create(
            minDisparity=0, numDisparities=num_disp, blockSize=block,
            P1=8 * block * block, P2=32 * block * block,
            uniquenessRatio=5, speckleWindowSize=50, speckleRange=2,
            disp12MaxDiff=1, mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)

    def disparity(self, rect_left, rect_right, num_disp: int = 128, block: int = 9):
        """Rectified pair -> float32 disparity in pixels (<=0 means no match)."""
        if self._sgbm is None:
            self._sgbm = self._make_sgbm(num_disp, block)
        return self._sgbm.compute(rect_left, rect_right).astype(np.float32) / 16.0
