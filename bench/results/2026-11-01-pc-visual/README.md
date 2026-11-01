# PC camera and replay evidence — 2026-11-01

The flight trace is an **initial functional trial**, not qualification of the
final controller. It uses the real PX4/Gazebo RGBD camera, PC OpenCV DNN YOLOX,
matched depth, ROS 2 commands and actual simulator telemetry. It contains no
actor-pose input. 83/124 follow samples commanded movement; the injected camera
pause held after the freshness timeout, and final telemetry was landed/disarmed.
Manual selection and Takeoff events occurred during the run and interrupted
orbit. Do not count it as a complete circle. Subsequent changes added yaw
setpoint smoothing, no-op repeated Takeoff, ONNX Runtime, and restored the Pi
demo's range mapping/appearance-return logic; those changes have unit coverage,
not a repeat of this exact live trial. Implementation was uncommitted during
the run; the final commit alone is not a source pin for that earlier trace.

Replay reports use the first 120 decoded frames of existing VisDrone SOT
`uav0000086_00870_s`. Raw per-frame rows are gzip-compressed here. Both runs
matched the annotated target in 108/120 frames, with 4 loss events and 3 changes
of matched track ID. These are SOT coverage/identity proxies, not multi-object
IDF1 or HOTA. Ground truth is read only by the scorer, never detector/tracker.

| Run | Engine | Processing FPS | Decode-through-tracking p95 |
|---|---|---:|---:|
| visdrone-086-first | OpenCV DNN | 2.725565 | 415.155624 ms |
| replay-20261101-122230-37b58e | ONNX Runtime CPU | 6.955585 | 155.628795 ms |

These are separate functional runs with different concurrent PC workloads
(live PC detection was running in the first, paused in the second). They are
**not a controlled speedup A/B** and are not Hailo throughput measurements.
Both model and decoded source hashes are recorded in the JSON reports.

An additional saved-Gazebo-frame micro-test ran 8 inferences per engine,
discarding the first for the median. ORT median 177.025243 ms; OpenCV DNN
311.530791 ms. Both detected the same person at confidence approximately .882.
This short sequential micro-test does not establish sustained performance.

The final environment is listed in bench/versions.md and the model card. No
continuous PC thermal/power trace was collected. Appearance return and gestures
need a real operator walk-test; no physical gesture success rate is claimed.
