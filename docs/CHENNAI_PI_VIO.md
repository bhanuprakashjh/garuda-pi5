# Run handheld odometry live on the Raspberry Pi 5

The OAK supplies stereo images and IMU samples over USB 3. A native Basalt process on the **Pi 5** estimates the handheld camera's path while you move it. The Pi writes poses and timing to a local run folder. You can disconnect the laptop after starting from a local Pi terminal. With SSH, keep the terminal connected until the summary appears.

## Prepare the Pi once

Use the 64-bit Pi OS, OAK Python environment and USB setup in the [receiving guide](../README.md). Provide active cooling and several GB of free disk for the source build. From the extracted kit on the Pi:

```sh
cd ~/chennai-oak
bash hosts/pi5/vio/build_basalt.sh
```

The build downloads pinned Basalt and its dependencies, compiles the ARM runner, and writes settings under `~/garuda-vio-build/config/`. Allow roughly 75 minutes on a Pi 5 for the initial build. If prerequisites are missing, the script prints the apt command to install them. Run it and repeat the build. A completed build prints `BUILD_OK`. If reusing a compatible build in another location, set `GARUDA_VIO_BUILD` to the directory containing `basalt/` and `config/`.

## Make a live walk

1. Place the OAK on a steady surface facing a well-lit, textured area. Check cable slack. Use this handheld session separately from the tabletop detector check. Close a foreground preview first. For systemd services, the wrapper pauses active camera owners and restores them at exit. If your fork uses different names, set `OAK_CAMERA_SERVICES` to those space-separated service names.
2. In the Pi terminal run:

   ```sh
   cd ~/chennai-oak
   run_id=walk-$(date +%Y%m%d-%H%M%S)
   GARUDA_VIO_CONFIG=oak-shipped bash hosts/pi5/vio/vio-session.sh live 60 "$run_id"
   ```

3. Let the camera settle for a few seconds, walk slowly away and back, then return it to the starting position and orientation. Leave it still for the final few seconds. The wrapper uses sudo for its temporary systemd job. It saves pose and gyro files. Use the separate recorder when you need to retain raw images for calibration.
4. When the run ends, inspect the result and check that your previous preview returns.

   ```sh
   python3 -m json.tool "$HOME/garuda-vio/runs/$run_id/summary.json"
   head -5 "$HOME/garuda-vio/runs/$run_id/trajectory.txt"
   ```

A full 60-second live take should show `mode: camera`, `stopped_by: time`, runner exit 0 and advancing states. Compare states, pairs sent, unpaired frames and dropped frames. Review temperature, dropped samples, and median/p95 latency. The feeder stops at its 80 °C threshold. A thermal stop or a short trajectory is an incomplete take. Check cooling, USB, lighting and camera service ownership before repeating. Choose a fresh run name each time.

## View the result and compare runs

Copy the entire `~/garuda-vio/runs/<run_id>/` folder to the laptop. Open `python3 oak-workstation.py`, choose **View a Pi VIO path**, and select that folder. It plots the Pi's saved positions directly. The laptop only needs OpenCV, NumPy and Zenity for this step. Keep `summary.json`, `trajectory.txt`, `poses.jsonl`, `gyro.csv` and `basalt_calib.json` together.

For comparison, the Pi runner can process an existing raw recording at the camera's original pace. Use a complete recording made with `record-handheld` and substitute its folder name below.

```sh
read -rp 'Full raw recording directory: ' session
GARUDA_VIO_CONFIG=oak-shipped bash hosts/pi5/vio/vio-session.sh replay "$session" "replay-$(date +%Y%m%d-%H%M%S)"
```

## Use the calibration measured at Chennai

Do the two AprilGrid fits on the workstation as described in [Start Here](../START_HERE.html). Copy the **camera–IMU fit** folder to the Pi, confirm the camera serial and 640 × 400 capture mode, and set `GARUDA_VIO_CALIB` to the full path of its `calibration.json` before running the live command again. Keep the noise selection and board measurements with that fit. A supplied calibration can still contain exploratory IMU noise defaults. Independent measured walks establish accuracy on this unit.

The Pi computes live odometry. The workstation also supports offline replay, sparse mapping, calibration fitting and tape scoring. These are separate bench sessions.
