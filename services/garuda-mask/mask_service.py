#!/usr/bin/env python3
"""garuda-mask — native Hailo dynamic-object masking service.

Runs YOLOv5n-seg on the Hailo-10H (yolov5n_seg_h10.hef) in its own thread, turns the
dynamic-class instances (person, car, ...) into ONE dilated binary mask over the reference
(left) rectified frame, and publishes only the latest via MaskIPC. A VIO front end reads
the latest mask and drops keypoints inside it — never blocking on detection (RDS-SLAM pattern).

BENCH / EVALUATION (CLAUDE.md rule 6): the seg model is YOLOv5 (Ultralytics, GPL-3.0). Permitted
for bench/experimental under the 2026-08-02 relaxation; masks are consumed behind IFrontEnd and
tagged not-product-cleared. This service itself is our own code; only the model weights are GPL.

    python3 services/garuda-mask/mask_service.py --probe   # one-shot on a captured pair
    python3 services/garuda-mask/mask_service.py           # live: CamArray -> MaskIPC loop
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

# COCO-80 classes considered dynamic for VIO masking (the YOLO_ORB_SLAM3 set, extended).
DYNAMIC_CLASSES = {
    "person", "bicycle", "car", "motorcycle", "bus", "train", "truck", "boat",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe",
}

DEFAULT_HEF = "/usr/share/hailo-models/yolov5n_seg_h10.hef"
DEFAULT_JSON = "/usr/share/hailo-models/yolov5seg.json"
DEFAULT_IPC = str(Path.home() / ".garuda-dynmask.shm")
DILATE_PX = 12          # absorbs the 1-few-frame mask lag while detection catches up (DynaSLAM buffer)


def build_dynamic_mask(instances, dynamic, out_hw, dilate_px):
    """instances: list of (class_name, inst_mask 2D truthy-where-object). Returns an out_hw
    uint8 mask: 1 where ANY dynamic-class instance is, resized to out_hw and dilated."""
    H, W = int(out_hw[0]), int(out_hw[1])
    acc = None
    for name, inst in instances:
        if name not in dynamic:
            continue
        m = (np.asarray(inst) > 0).astype(np.uint8)
        if m.shape != (H, W):
            m = cv2.resize(m, (W, H), interpolation=cv2.INTER_NEAREST)
        acc = m if acc is None else np.maximum(acc, m)
    if acc is None:
        return np.zeros((H, W), np.uint8)
    if dilate_px > 0 and acc.any():
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate_px + 1, 2 * dilate_px + 1))
        acc = cv2.dilate(acc, k)
    return acc


def _probe(hef, json_path):
    """One-shot: run seg on a captured stereo tile, report detections + save a mask overlay."""
    import sys
    import time
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "hosts" / "pi5"))
    from hailo_yolo_seg import HailoYoloSeg, to_seg_input

    caps = repo / "calib" / "captures"
    tile = cv2.imread(str(caps / "pair11_L.png"), cv2.IMREAD_GRAYSCALE)
    if tile is None:
        raise SystemExit(f"no probe image at {caps/'pair11_L.png'}")
    H, W = tile.shape[:2]
    net = HailoYoloSeg(hef, json_path)
    rgb = to_seg_input(tile)
    # warm + time
    for _ in range(2):
        dets = net.infer_instances(rgb)
    n, t0 = 10, time.monotonic()
    for _ in range(n):
        dets = net.infer_instances(rgb)
    dt = (time.monotonic() - t0) / n
    names = [d[1] for d in dets]
    mask = build_dynamic_mask([(nm, im) for (_, nm, _, im) in dets],
                              DYNAMIC_CLASSES, (H, W), DILATE_PX)
    print(f"detections={len(dets)} classes={sorted(set(names))}")
    print(f"dynamic-mask coverage={100*mask.mean():.1f}%  infer={1e3*dt:.1f} ms  ({1/dt:.1f} fps)")
    over = cv2.cvtColor(tile, cv2.COLOR_GRAY2BGR)
    over[mask > 0] = (0, 0, 255)
    out = repo / "bench" / "mask_probe.png"
    cv2.imwrite(str(out), cv2.addWeighted(cv2.cvtColor(tile, cv2.COLOR_GRAY2BGR), 0.6, over, 0.4, 0))
    print(f"wrote overlay {out}")
    net.close()


def _serve(hef, json_path, ipc_path):
    """Live loop: newest CamArray left tile -> seg -> dilated dynamic mask -> MaskIPC (latest-wins)."""
    import sys
    import time
    from mask_ipc import MaskIPC
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "hosts" / "pi5"))
    sys.path.insert(0, str(repo / "services" / "garuda-camera"))
    from camarray_backend import CamArrayBackend           # noqa: F401 (hardware import)
    from hailo_yolo_seg import HailoYoloSeg, to_seg_input
    from stereo_rig import StereoRig

    rig = StereoRig(repo / "bench" / "camarray_stereo.yaml")
    net = HailoYoloSeg(hef, json_path)
    cam = CamArrayBackend()
    cam.start()
    ipc = None
    seq = 0
    try:
        while True:
            frame = cam.read()                              # (tile0, tile1, t_ns) per backend
            if frame is None:
                time.sleep(0.001)
                continue
            left_tile, _right, t_ns = frame
            rc1, _ = rig.rectify(left_tile, _right)
            H, W = rc1.shape[:2]
            if ipc is None:
                ipc = MaskIPC(ipc_path, shape=(H, W), create=True)
            dets = net.infer_instances(to_seg_input(rc1))
            mask = build_dynamic_mask([(nm, im) for (_, nm, _, im) in dets],
                                      DYNAMIC_CLASSES, (H, W), DILATE_PX)
            seq += 1
            ipc.write(seq, t_ns, mask)
    finally:
        cam.stop()
        net.close()
        if ipc is not None:
            ipc.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hef", default=DEFAULT_HEF)
    ap.add_argument("--json", default=DEFAULT_JSON)
    ap.add_argument("--ipc", default=DEFAULT_IPC)
    ap.add_argument("--probe", action="store_true", help="one-shot on a captured pair, then exit")
    a = ap.parse_args()
    if a.probe:
        _probe(a.hef, a.json)
    else:
        _serve(a.hef, a.json, a.ipc)


if __name__ == "__main__":
    main()
