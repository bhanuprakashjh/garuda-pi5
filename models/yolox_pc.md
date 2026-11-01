# YOLOX-S PC evaluation detector

This is the optional **PC CPU** detector for Gazebo camera pixels and recorded
footage. It does not run on Hailo-10H and does not replace the live Pi models.

- Source: OpenCV Zoo `models/object_detection_yolox`, revision
  `48b10fa505aeb51fbd127fc60a5c660040e823b7`.
- File: `object_detection_yolox_2022nov.onnx`, input RGB 640×640, top-left
  letterbox padded with 114, float32 unnormalized tensor.
- SHA-256: `c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063`.
- Model/code licence: Apache-2.0. Fetcher verifies the upstream Git LFS hash
  and stores the upstream licence alongside the model in `.runtime/models/yolox`.
- Runtime: ONNX Runtime 1.23.2, CPUExecutionProvider, four intra-op threads.
  OpenCV is retained for resize, NMS and decoding; the initial comparison used
  OpenCV DNN on the identical model and source pixels.
- Person-only COCO class 0; confidence threshold .35, NMS IoU .45. Flight
  requires track confidence .5 and fresh observations. No pose/gesture output.
- Tracker: existing repository IoU tracker, max_age 3 frames. This is not
  Hailo-15's OSNet identity tracker. Do not transfer its identity benchmarks.

The source model licence is documented at
https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/object_detection_yolox .
ONNX Runtime: MIT, https://github.com/microsoft/onnxruntime/blob/v1.23.2/LICENSE .
OpenCV: Apache-2.0; NumPy: BSD-3-Clause; Pillow: HPND; coloredlogs/humanfriendly:
MIT; flatbuffers: Apache-2.0; packaging: Apache-2.0 OR BSD-2-Clause;
SymPy/mpmath: BSD-3-Clause. Runtime package inventory is local bench only and
is not a product distribution approval.

Reused world/camera work: Hailo-15 branch `b57dd5e`,
`bench/sim/gazebo_follow.sdf` and `gazebo_vision.py`. The world uses the existing
cached **Mingfei actor v1**, CC BY 4.0,
https://fuel.gazebosim.org/1.0/Mingfei/models/actor/1 . The mesh is unmodified;
this experiment changes its trajectory. Attribution and licence metadata were
recorded by the Hailo-15 project in `docs/dependencies/gazebo-follow-bench.md`.
No model assets or dataset images are redistributed in this branch.

VisDrone sequences are the existing local research dataset in `hailo-export`.
Reports are evaluation artifacts, not permission to redistribute the dataset
or use research-only imagery in a public trade-show display.
