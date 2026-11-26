# Chennai OAK-D Pro field kit

**Start with a tabletop tracking check using your working CM3/Pi/Hailo installation.** You can complete it before installing Basalt or measuring the camera calibration. Keep your local tracker checkout and its working CM3 profile. This kit supplies the OAK camera input, stereo depth tools, onboard H.265 video, handheld recorder and desktop calibration launcher. The receiving engineer owns the integration into the Chennai tracker.

| Where | What to run | What you should see |
| --- | --- | --- |
| Pi 5 on the bench | OAK preview and your existing detector | Moving OAK RGB image, fresh detections on a test object and an aligned stereo depth view |
| Linux laptop | Browser and H.265 receiver | Bench displays on the Pi and a separate decoded video window |
| Pi 5, handheld OAK | `bash record-handheld walk 90` | A fresh folder containing stereo pairs, IMU readings and capture settings |
| x86-64 Linux desktop | `python3 oak-workstation.py` | A plotted handheld path, then separate calibration and validation results |

## 1. Download and copy the kit

On the release page download the **GARUDA-Chennai-OAK-….zip** asset and **SHA256SUMS.txt** into the same new folder. On a Linux laptop, open a terminal in that folder.

```sh
sha256sum -c SHA256SUMS.txt
unzip GARUDA-Chennai-OAK-*.zip
cd GARUDA-Chennai-OAK-*/
sha256sum -c SHA256SUMS
xdg-open START_HERE.html
```

Each checksum should report `OK`. The guide opens offline. The tag on GitHub also contains the source, guides and printable targets. On the laptop, set the Pi SSH destination to your station's real account and address. The example destination directory on the Pi should be new for this release. Pick a fresh name if you already have a kit there and use it in the following commands.

```sh
read -rp 'Pi SSH destination (user@hostname): ' PI_SSH
ssh "$PI_SSH" 'mkdir ~/chennai-oak' && scp -r . "$PI_SSH:~/chennai-oak/"
ssh "$PI_SSH" 'cd ~/chennai-oak && sha256sum -c SHA256SUMS'
```

## 2. Set up the bench

**On the laptop** (Ubuntu/Debian desktop), install the launcher and video receiver dependencies.

```sh
sudo apt install zenity python3-numpy python3-opencv gstreamer1.0-tools \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-libav gstreamer1.0-x
```

**On the Pi** (64-bit Raspberry Pi OS), keep the existing Hailo installation. Connect the OAK-D Pro over USB 3. Check the camera appears in `lsusb` and check its link speed in `lsusb -t`. For a Pi that has only used CM3, install the camera dependencies below. Use the existing OAK environment if the station already has it.

```sh
cd ~/chennai-oak
sudo apt install python3-venv python3-opencv python3-numpy usbutils \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
python3 -m venv --system-site-packages ~/.venvs/oak-bringup-3.12.1
~/.venvs/oak-bringup-3.12.1/bin/python -m pip install --only-binary=:all: depthai==3.12.1
~/.venvs/oak-bringup-3.12.1/bin/python -c 'import cv2, depthai, numpy; print(depthai.__version__)'
```

If the camera is visible only to root, have the station administrator install the [USB rules](hosts/pi5/oak/80-garuda-oak.rules) in `/etc/udev/rules.d/` with the local camera-access group, grant the station user membership, reload the rules, reconnect the OAK and start a new login session. Both boot states of the device need access.

## 3. Verify the OAK input, stereo distance and video

1. With the flight controller disconnected, save a screenshot of a suitcase or other recognizable object in the working CM3 tracker. Write down the profile, service names and tracker revision. Keep these for the return-to-CM3 check.
2. Start the H.265 receiver **on the laptop**. Use a wired or Wi-Fi interface reachable from the Pi. The command listens on UDP 5600 until you press Ctrl+C.
3. Start the camera owner **on the Pi** in a separate terminal using the laptop's reachable IP address. If the installed OAK owner already uses these options, use that instance. A single process owns the OAK USB device.
4. In the laptop browser visit `http://PI_HOST:8096/`, replacing `PI_HOST` with the actual Pi address. Move the object and confirm the preview and separate H.265 picture both change. The encoded video shows the camera image before detector overlays.
5. In the team's existing bench tracker, select its OAK RGB input. On the stock GARUDA detector the source selector is `GARUDA_MONO_CAM=oak`. If the local fork predates that selector, its camera engineer can compare the [RGB capture adapter](hosts/pi5/oak_capture.py) and the installed camera input. The adapter supplies 1280 × 720 BGR frames at 15 fps. Compare outputs before changing the model or tracker. Move the object across the frame, briefly cover the OAK colour lens, then restore the view. Confirm the active source and a fresh moving detection. The stock demo lives at `http://PI_HOST:8098/demo`.
6. Open `http://PI_HOST:8099/camera` and use the [operator field guide](hosts/pi5/demo/chennai/OPERATOR.md) for board depth versus tape. If that service is absent, run `python3 hosts/pi5/oak_calibration.py` in another Pi terminal from the kit directory. Save the depth export and bench receipt. Restore the CM3 profile and repeat the reference check.

**Laptop receiver**

```sh
gst-launch-1.0 -v udpsrc port=5600 \
  caps='application/x-rtp,media=video,encoding-name=H265,payload=96,clock-rate=90000' \
  ! rtpjitterbuffer ! rtph265depay ! h265parse ! avdec_h265 ! videoconvert ! autovideosink sync=false
```

**Pi camera owner**

```sh
cd ~/chennai-oak
read -rp 'Laptop receiver IP address: ' RECEIVER_IP
~/.venvs/oak-bringup-3.12.1/bin/python hosts/pi5/oak/preview.py \
  --port 8096 --host-stereo --map-on-demand --map-interval-ms 500 \
  --video-codec h265 --video-host "$RECEIVER_IP" --video-port 5600
```

If RGB works but the receiver stays blank, check the Pi's destination IP, laptop UDP 5600 access, decoder plugins and the video counter in the launcher's **Check bench camera signals**. If detector counters are unavailable from a custom API, save a screenshot and the tracker revision with your observation.

## 4. Record a walk and see the path

**On the Pi**, close any foreground OAK preview you started above. If systemd manages the preview and detector, the recorder pauses the named active services and requests their restart afterward. For custom service names set `OAK_CAMERA_SERVICES` to a space-separated list of your station's camera services. Record one slow out-and-back handheld walk in a textured room, pausing at both ends.

```sh
cd ~/chennai-oak
bash record-handheld walk 90
```

**On the laptop**, copy the entire folder printed by the recorder. Enter the full remote path including its recording ID. Keep the two image folders and their matching IMU, timestamp and calibration files together.

```sh
mkdir -p ~/oak-recordings
read -rp 'Full recording path printed on the Pi: ' TAKE_PATH
scp -r "${PI_SSH}:${TAKE_PATH}" ~/oak-recordings/
```

For replay and fitting, download the **basalt-0.1.7-x86_64-unknown-linux-gnu.tar.gz** binary and its matching **.sha256** file from the [Basalt 0.1.7 release](https://gitlab.com/VladyslavUsenko/basalt/-/releases/0.1.7). In the download folder, verify the publisher's digest, then extract into a fresh tools folder.

```sh
name=basalt-0.1.7-x86_64-unknown-linux-gnu.tar.gz
printf '%s  %s\n' "$(awk '{print $1}' "$name.sha256")" "$name" | sha256sum -c - &&
  mkdir -p ~/oak-tools/basalt-0.1.7 && tar xzf "$name" -C ~/oak-tools/basalt-0.1.7
```

From the extracted **GARUDA kit** directory, run `python3 oak-workstation.py`. Choose **Try a handheld replay**, then select `~/oak-tools/basalt-0.1.7/release`, your copied walk, and a results folder. A successful replay opens `run/path.png` and saves `run/trajectory.txt` and `run/replay.json`. This first path uses factory calibration and exploratory IMU parameters.

## 5. Fit this unit, validate and return results

Use the [offline operator instructions](START_HERE.html) for the full sequence and the [engineering guide](docs/CHENNAI_OAK_MAPPING.md) for equivalent commands, saved files and map inspection.

1. Print the [VIO AprilGrid](hosts/pi5/demo/chennai/oak-aprilgrid-a4.pdf) on A4 at actual size. Mount it flat and measure the tag edge, gap and scale bar. Keep the separate [depth-check targets](hosts/pi5/demo/chennai/oak-targets-a4.pdf) for the earlier range check.
2. On the Pi record `bash record-handheld camera-grid 90` with the grid filling different parts of both images. Then record `bash record-handheld imu-grid 90` with the fixed board visible as you rotate the camera about all three axes. Copy both takes as above.
3. On the laptop choose **Fit stereo cameras**, enter the measured board dimensions in millimetres, inspect detections and residuals, save in Basalt and close its window. Choose **Fit camera and IMU** with the separate moving take and the saved stereo result. Record the IMU noise source and inspect the pose and time-offset fit. Keep both result folders under the unit's serial.
4. Mark and measure a straight A–M–B route. Record two fresh walks A → M → B → M → A with pauses at each mark. For each, choose **Replay a measured calibration and build a map**, then **Score a measured walk**. Compare the reported stops with your notes and the distances against the tape.
5. Return the tracker revision, camera ID, CM3/OAK observations, receiver result, measured depth export, board measurements, fit folders, trajectory plots and validation scores. Include failed takes and their logs. Keep room imagery in your team's chosen evidence store.

A replay that draws a path confirms the software ran. Use independent tape measurements and repeat walks to judge accuracy on this camera. For troubleshooting, inspect the stage's `operator.log` and the relevant section of `START_HERE.html`.
