# yolov5n-seg (Hailo-10H) — model card

- **Task:** instance segmentation → dynamic-object masking for VIO (person, car, bicycle, …).
- **HEF:** `/usr/share/hailo-models/yolov5n_seg_h10.hef` (+ `yolov5seg.json`). Ships with the Hailo
  model zoo on the bench image — **not** fetched into `models/` (so no `fetch.sh` row).
- **Licence:** **GPL-3.0** (Ultralytics YOLOv5). **EVAL-ONLY** — bench/experimental under the
  2026-08-02 relaxation. Consumed only behind `IFrontEnd` (via `garuda-mask`), tagged
  `not-product-cleared`. Never wired into a shippable `services/` path. Product tier needs a
  permissively-licensed detector/segmenter (e.g. an Apache/BSD YOLO or a clean retraining).
- **I/O:** input 640×640×3 UINT8; outputs `conv63` 160×160×32 (mask prototypes) + three detection
  heads (20/40/80 grid, 351 ch = 3 anchors × (4 box + 1 obj + 80 cls + 32 mask coeff)). The full
  YOLOv5-seg decode (grid/anchor box decode → NMS → `sigmoid(coeff·proto)` mask) runs on the CPU in
  `hosts/pi5/hailo_yolo_seg.py`.
- **Measured (bench 2026-08-02):** synchronous single-frame **83.8 ms NPU + 3.1 ms CPU decode =
  11.5 fps**. NPU-latency-bound (4-context model, blocking `run()`); pipelined/async throughput is
  `TBD-bench`. See `bench/benchlog.md`.
- **Consumer:** `services/garuda-mask/mask_service.py` → dilated dynamic mask → `MaskIPC` (latest-wins)
  → ORB-SLAM3 keypoint rejection (Task 3 of the hybrid plan).
