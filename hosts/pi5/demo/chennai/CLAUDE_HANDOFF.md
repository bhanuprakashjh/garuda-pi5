# Chennai OAK camera and handheld mapping handoff

For the guided bench checks, desktop calibration dialogs and printable VIO board, open [Start here](../../../../START_HERE.html). The commands below are the equivalent engineering workflow.

Use this add-on alongside the team's existing CM3/Pi/Hailo installation. Keep the CM3 changes in their own checkout. The live camera service and the handheld recorder each open the USB camera. Schedule them as separate sessions. The commands below run from the root of the extracted add-on or checkout.

## Camera display, object detection and stereo

| Function | Source and processor |
| --- | --- |
| Single-image object detection | OAK colour camera CAM_A supplies the RGB view to the existing Pi/Hailo detector. “Mono” here means one image. The detector uses colour. |
| Distance preview | CAM_B and CAM_C supply a synchronized monochrome stereo pair. The Pi computes disparity and projects depth into the RGB view. The displayed optical-axis depth needs physical validation against a measured target. |
| Video downlink | The OAK hardware encoder produces H.265/HEVC. The Pi relays RTP. The browser MJPEG preview is a separate output. |
| Handheld mapping | The recorder uses the raw CAM_B/C pair and IMU. An offline Basalt run produces a camera-rig trajectory and keyframe data for mapping. |

The camera owner in `hosts/pi5/oak/preview.py` supports the host-stereo and H.265 options together. Reuse the installed detector and select its OAK RGB source. The station guide describes the existing profiles and how to check range with printed targets. In the November 14 bench run, the detector produced 15.2 fps and the OAK video relay reported 1,592 of 1,592 encoded frames sent. That specific receiver check counted RTP packets. Chennai's CM3 flight-control changes are maintained in its separate installation.

See [the Chennai camera station guide](../../../../hosts/pi5/demo/chennai/OPERATOR.md).

On the bench, the existing OAK owner can run the three camera outputs together. Use the Pi's OAK environment and substitute the video receiver's IP address. Release the USB camera from any running preview service before starting this foreground check.

```sh
"$HOME/.venvs/oak-bringup-3.12.1/bin/python" hosts/pi5/oak/preview.py --port 8096 --host-stereo --map-on-demand \
  --map-interval-ms 500 --video-codec h265 --video-host "${RECEIVER_IP:?}" --video-port 5600
```

The source adapter `hosts/pi5/oak_capture.py` reads the local RGB feed for the installed detection application. Check detections on a stationary object and compare its displayed stereo depth with a tape reading. Check the receiver picture independently. The H.265 stream carries the sensor image upstream of the detector overlay.

## Record three kinds of handheld data

Use the Pi's DepthAI 3.12.1 environment with OpenCV and NumPy; check that the OAK can be opened as the station user. Stop the OAK preview service while recording and restore it afterward if it was running. Label each session with the camera serial, resolution, date and purpose. Keep the original session directories.

1. **Camera calibration.** Record a stationary AprilGrid at varied positions, angles and distances across *both* images. Pause at each view. Enter the measured tag edge length in metres and gap-to-edge ratio in a Basalt AprilGrid JSON matching the physical board. The checkerboards in the station guide are for the separate depth check.
2. **Camera–IMU calibration.** Record a separate take with the same grid fixed and visible, moving the camera through rotations about all three axes and translations. Keep the stereo mode and resolution identical. Retain the IMU noise inputs and their source with the solution.
3. **Independent evaluation.** Record a marked handheld route with a start, midpoint and endpoint. Pause at the marks and return to the start in the same orientation. Record the tape distances and pause times. Keep a second take separate from any tuning. A stationary smoke capture is useful for checking installation.

Run the recorder for each take with a fresh output name. The recorder defaults to 640 × 400 and 30 fps.

```sh
python3 bench/oak_vio_record.py --seconds 120 --out recordings/camera-grid
python3 bench/oak_vio_record.py --seconds 120 --out recordings/imu-grid
python3 bench/oak_vio_record.py --seconds 120 --out recordings/walk-held-out
```

## Fit the calibration at Chennai

Copy the recordings to the workstation. Conversion requires Python 3, NumPy and OpenCV. The calibration and replay commands require Basalt tools; the tested workstation replay used version 0.1.7. For an unpacked Basalt release, put its `bin` directory on PATH and its `lib` directory on LD_LIBRARY_PATH. Set `BASALT_CONFIG` to its `data/euroc_config.json`.

```sh
python3 bench/oak_vio_convert.py recordings/camera-grid datasets/camera-grid --for-calibration
python3 bench/oak_vio_convert.py recordings/imu-grid datasets/imu-grid --for-calibration
mkdir -p calibration
basalt_calibrate --dataset-type euroc --dataset-path datasets/camera-grid \
  --aprilgrid measured-grid.json --result-path calibration/ --cam-types ds ds
basalt_calibrate_imu --dataset-type euroc --dataset-path datasets/imu-grid \
  --aprilgrid measured-grid.json --result-path calibration/ \
  --accel-noise-std "${ACCEL_NOISE:?}" --gyro-noise-std "${GYRO_NOISE:?}" \
  --accel-bias-std "${ACCEL_BIAS_STD:?}" --gyro-bias-std "${GYRO_BIAS_STD:?}"
```

In the first GUI, detect the grid corners, initialize intrinsics, poses and extrinsics, optimize, inspect corner coverage and reprojection residuals, then save. In the second, load the camera solution, detect corners, initialize camera poses and the camera–IMU transform, optimize and inspect the time-offset fit before saving. Set the four shell variables above to the team's characterized noise values in Basalt's units. Archive the resulting `calibration/calibration.json` with the camera serial and both calibration takes.

## Replay and inspect the map

```sh
python3 bench/oak_vio_convert.py recordings/walk-held-out datasets/walk \
  --basalt-calib calibration/calibration.json
python3 bench/oak_vio_replay.py datasets/walk runs/walk --config "${BASALT_CONFIG:?}" --map
```

The raw path copies the paired PNGs byte-for-byte. For measured replay the converter shifts both camera timestamps by the fitted camera-to-IMU offset and sets the effective replay offset to zero. It preserves the supplied file as `source_calibration.json` and records its SHA-256 and applied shift in `conversion.json`. Check the serial and capture mode yourself; the native calibration format does not establish device identity.

For a pre-calibration smoke test, omit `--basalt-calib` and use a fresh dataset name. That path uses factory-undistorted images and placeholder IMU parameters. Keep factory and measured runs separately.

Each replay uses a fresh run directory. Inspect `trajectory.txt` (TUM timestamp, translation in metres and quaternion), `replay.json`, the engine logs and the `marg/` keyframe cache. The headless mapper runs feature matching and optimization. To inspect the sparse map interactively, from the run directory launch `basalt_mapper --cam-calib basalt_calib.json --config-path config.json --marg-data marg --show-gui 1`, then use Detect, Match, Tracks and Optimize. The saved TUM file is the odometry trajectory. The mapper GUI can separately save the optimized keyframe trajectory.

Compare pose coverage, displacement between marked rests and return displacement against the tape without fitting a scale. Save the measured distances, settings, original calibration, reports and logs together. Real-time performance, held-out accuracy and repeatability on the shipped unit remain to be measured.

For a walk with stops A, M, B, M, A, use the tape scorer below. Set `AM_METRES` and `MB_METRES` to your measured segment lengths. Check the reported gyro rest windows against the operator's notes, since a gyro can also be quiet while the camera translates.

```sh
python3 bench/oak_vio_score.py --trajectory runs/walk/trajectory.txt \
  --gyro datasets/walk/mav0/imu0/data.csv --route A M B M A \
  --taped "A-M=${AM_METRES:?}" --taped "M-B=${MB_METRES:?}" --out runs/walk/tape-score.json
```
