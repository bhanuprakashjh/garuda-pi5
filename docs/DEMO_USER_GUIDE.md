# Microchip / GARUDA demo: installation and operator guide

Updated 2026-11-01. Covers the Hailo-10H Pi camera demo and the separate PC ROS 2 / PX4 / Gazebo flight lab through commit `1d28872`. Start here for the current demo; older bench instructions describe earlier experiments.

**Publication status:** published to the public `bhanuprakashjh/garuda-pi5` `main` on 2026-11-01 together with the Pi demo and PC flight-lab sources (Pi baseline `99db1c5`, lab through `1d28872`); the repository's original October 24 snapshot remains in its history. The remaining release gates (fresh-machine installation, full-orbit and cooled-soak validation) are listed in the [repository update plan](GARUDA_PI5_UPDATE_PLAN.md) and are still open.

For the automated testing installer and downloadable executable/source kit, see
[release setup](../release/README.md). Its pinned model manifest supersedes broad
model fetching: it verifies exact baseline bytes, including the known-good pose
artifact served under a v5.4.0 URL. The manual steps below remain useful for diagnosis.

The existing bench works. A complete installation on a second, clean PC/Pi has **not** been validated. In particular, the Pi requires compatible compiled models and Hailo packages; those are not all supplied by a single installer. This guide identifies those dependencies rather than silently substituting incompatible downloads.

## 1. Choose what to run

| Mode | Required hardware/software | What moves |
|---|---|---|
| Original demo at Pi `:8098/demo` | Pi 5, Hailo-10H, camera, browser | Browser-drawn quad only |
| PX4 lab with live Pi camera | Original demo plus Linux PC prerequisites below | PX4 SITL aircraft in Gazebo; target derived from Pi images |
| PX4 lab with drone camera | PC prerequisites, camera-equipped Gazebo world | PX4 SITL using rendered RGBD and PC CPU detection |
| Recorded aerial footage | PC vision environment, model, video/dataset | Analysis only; flight commands disabled |

Microchip is the demo branding; Hailo-10H performs Pi detection/pose inference. No Microchip CA90 flight-controller board is driving this demonstration. PX4 runs on the PC. The Gazebo/replay detector is YOLOX-S on **PC CPU**, not Hailo. See [handover](DEMO_HANDOVER.md) for architecture and measured evidence.

## 2. Supported baseline and prerequisites

| Component | Recorded working baseline |
|---|---|
| Pi | Pi 5 8 GB, Raspberry Pi OS Trixie 64-bit, Python 3.13.5 |
| Accelerator | Hailo-10H / AI HAT+ 2; runtime, firmware, driver and Python binding 5.3.0 |
| Camera | Lenovo FHD USB webcam; V4L2 1280×720. CM3 repair pending |
| PC | Ubuntu 24.04.4 LTS x86_64, desktop display and graphics capable of Gazebo rendering |
| Gazebo | Harmonic, gz-sim 8.10.0; transport13 and msgs10 bindings |
| PX4 | `47ce421a88ca870eb8d55eccac5f80fc9b368948` |
| ROS | Jazzy via project-local RoboStack; exact environment in `pixi.lock` |
| Pixi | 0.83.0 |
| XRCE agent | v2.4.3, `73622810d984349b80bbac0ef55fc0b694d62222` |
| QGroundControl | Existing x86_64 AppImage; exact release not recorded, pin and record it on new installations |
| PC vision | Python 3.12.3; pinned packages in `hosts/pc/vision-requirements.txt` |

Ubuntu 22.04 is supported by upstream Gazebo/PX4/QGC, but this integration was tested on 24.04. Windows, macOS and ARM PCs are not validated; the Pixi lock targets `linux-64`. Have internet access for initial downloads, adequate disk space for source/builds/environments, and a working Ethernet connection to the Pi. Fit active cooling before sustained Pi operation; the current passive heatsink has throttled under load. Use a suitable Pi power supply.

Installation commands below are for a **new installation**, not an instruction to upgrade the working booth Pi. Keep the preserved baseline intact.

## 3. Prepare a new Pi

### OS, camera and network

Install 64-bit Raspberry Pi OS Trixie on a new SD card, create user `garuda`, enable SSH, and connect the Pi and PC by Ethernet or the same local network. The existing mono code contains `/home/garuda/garuda-sdk-deploy` paths: use this account and deployment path until Pi path portability is implemented. The PC paths, in contrast, are configurable.

The existing bench uses `garuda@10.42.0.128` through a PC NetworkManager shared profile named `pi-share`. Another computer may assign a different address; find it from its router/shared-network leases and enter that URL in the launcher. Do not copy a machine-specific interface name such as `eno1` blindly.

On the new Pi:

```bash
sudo apt-get update
sudo apt-get install git curl python3-numpy python3-opencv python3-venv \
  python3-pip rpicam-apps v4l-utils pciutils
sudo usermod -aG video garuda
```

Log out and back in after changing groups. Connect/reseat a CSI camera only with Pi power disconnected. For the working fallback, plug in a USB webcam. Inspect capture devices:

```bash
rpicam-hello --list-cameras
v4l2-ctl --list-devices
lspci
```

`GARUDA_MONO_CAM=auto` prefers detected CSI, otherwise selects USB, and re-probes when capture dies. Set `GARUDA_MONO_CAM=/dev/video0` to force a known USB capture node or `csi` to require CSI. Device numbering can change after reconnecting; choose a node that actually supports capture.

### Install a matched Hailo-10H stack

Raspberry Pi's official AI HAT+ 2 package family is `hailo-h10-all`; `hailo-all` is for the Hailo-8 family and must not be mixed with it. See [Raspberry Pi AI software documentation](https://www.raspberrypi.com/documentation/computers/ai.html).

For a new distribution-managed H10 installation, the package entry point is:

```bash
sudo apt-get install hailo-h10-all
sudo reboot
```

**That command does not guarantee the recorded 5.3.0 environment.** Check the candidate version before installation with `apt-cache policy hailo-h10-all`. The bench originally used distribution 5.1.1, then migrated on a spare card to the matching Hailo 5.3.0 ARM64 runtime/driver and CPython 3.13 ARM64 binding. Do not overlay two package families or install a random wheel over distribution bindings.

To reproduce the recorded 5.3.0 deployment, obtain the matching runtime `hailort_5.3.0_arm64.deb`, PCIe driver `hailort-pcie-driver_5.3.0_all.deb`, and CPython 3.13/aarch64 wheel from Hailo's supported distribution, with their installation instructions and checksums. These vendor artifacts are not bundled here. Use a clean matching stack and verify the package transition rather than blindly mixing the legacy h10 packages. [The recorded migration](decisions.md) explains the local kernel 6.18 DKMS timer-API failure. Apply that workaround only if the same build error occurs; do not apply historical PCIe/kernel fixes pre-emptively.

Before starting inference:

```bash
uname -a
python3 --version
hailortcli fw-control identify
python3 -c 'import cv2, numpy, hailo_platform; print(cv2.__version__, numpy.__version__)'
vcgencmd get_throttled
```

Identify must report HAILO10H with the intended firmware. The Python import must work for the same interpreter used by the service. A PCIe device visible in `lspci` but unavailable to HailoRT needs driver/runtime diagnosis before application debugging. Record exact versions in your installation log.

### Source and models

Place the reviewed demo source in `/home/garuda/garuda-sdk-deploy`. Copy the **whole source tree**, including `hosts/pi5`, `services/garuda-follow`, `services/garuda-track`, `models` and UI assets; copying only `garuda_mono.py` is insufficient. On a new clone, select the release/branch that includes this guide. The public October snapshot alone is insufficient for the new functionality.

Required files under that deployment:

| Path | Purpose / availability |
|---|---|
| `models/yolo26m.hef` | Default primary detector; obtain a Hailo-10H/5.3.0-compatible artifact |
| `models/yolov8m_pose_h10.hef` | Pose and gestures; obtain compatible compiled model |
| `models/yolo26m-depth_hailo_model/yolo26m-depth.hef` | Default `y26d` depth; custom build/output, not supplied by general fetch script |
| `models/depth_anything_v2_vits.hef` | Required at startup: code constructs both depth engines; `GARUDA_MONO_DEPTH=da` selects it and requires scene calibration |
| `models/yolo_world_v2s_h10.hef` | Optional open-vocabulary mode; snapshot contains this model |
| `models/faces/yunet.onnx`, `sface.onnx` | Optional CPU face matching; fetched below |

Both depth HEFs must be present even when `y26d` is selected, because startup currently constructs both engines. Lazy initialization would be a future improvement.

Use [model documentation](../models/) and [depth build procedure](../models/build_yolo26_depth_hef.sh) for provenance/build details. The depth compiler runs on an appropriate x86 build machine, not the Pi. Detector and pose paths can be overridden by `GARUDA_MONO_YOLO` and `GARUDA_MONO_POSE`; several other paths remain fixed. Preserve associated calibration/metadata when transferring known-good models.

**Do not use `models/fetch.sh` as a universal setup command:** it currently points to compiled v5.4.0 assets, while the recorded runtime is 5.3.0, and it does not fetch all required custom models. Compatibility and model hashes must be verified before release. Existing licensed model files from the preserved bench can be transferred for authorized bench use; redistribution is a separate release review. YOLO26/pose and some experimental models are not product-cleared.

Optional faces, from the deployment root:

```bash
python3 models/fetch_faces.py
```

This fetcher pins checksums and license files. Existing OpenCV provides YuNet/SFace; it adds no Hailo package upgrade. Face descriptors run on CPU. Enrollment belongs to the original demo's browser/session; it is not a central identity database or PX4 identity confirmation.

### Start and install the mono service

First run interactively as `garuda`, from the deployment root:

```bash
GARUDA_MONO_CAM=auto GARUDA_MONO_DEPTH=y26d \
  /usr/bin/python3 -u hosts/pi5/garuda_mono.py
```

Open `http://PI_ADDRESS:8098/demo` from the PC. An active stream viewer is needed for inference; an idle state endpoint alone is not a camera workload. Verify boxes and pose mode before installing autostart. Stop the interactive process with Ctrl-C so it releases the camera and port.

For a fresh installation, create `/etc/systemd/system/garuda-mono.service` with:

```ini
[Unit]
Description=GARUDA camera and browser demo (Hailo-10H)
After=network.target

[Service]
User=garuda
WorkingDirectory=/home/garuda/garuda-sdk-deploy
Environment=GARUDA_MONO_CAM=auto
Environment=GARUDA_MONO_DEPTH=y26d
ExecStart=/usr/bin/python3 -u /home/garuda/garuda-sdk-deploy/hosts/pi5/garuda_mono.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now garuda-mono
systemctl status garuda-mono --no-pager
journalctl -u garuda-mono -n 80 --no-pager
```

Preserve existing unit drop-ins when updating an installed system. `hosts/pi5/install.sh` is the older portal/configd/depth installer, **not a complete installer for this demo**. Its full mode also changes sudo policy and installs legacy GenAI tooling. Neither that GenAI stack, the portal on 8080, nor Pi-side ROS is required for this mono-camera-to-PC demonstration.

## 4. Prepare the PC flight lab

Run source-related commands from the reviewed GARUDA checkout root unless a subshell explicitly changes directory. Use your own paths. These examples keep external PX4 source under `~/Development` and generated dependencies under `.runtime` (Git ignored).

### Host packages and Gazebo Harmonic

```bash
sudo apt-get update
sudo apt-get install git curl ca-certificates gnupg lsb-release build-essential \
  cmake ninja-build pkg-config python3-dev python3-venv python3-pip unzip
sudo curl -fsSL https://packages.osrfoundation.org/gazebo.gpg \
  -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
printf '%s\n' "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] https://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/gazebo-stable.list >/dev/null
sudo apt-get update
sudo apt-get install gz-harmonic python3-gz-transport13 python3-gz-msgs10 \
  python3-protobuf
```

These use the [official Harmonic Ubuntu repository](https://gazebosim.org/docs/harmonic/install_ubuntu/) and its [transport Python bindings](https://gazebosim.org/api/transport/13/python.html). Do not substitute Gazebo Classic or an unrelated Gazebo release. Apt resolves currently available patch versions: record them and repeat validation if they differ from the baseline.

### Build the matching PX4 SITL

```bash
mkdir -p "$HOME/Development"
export GARUDA_PX4_DIR="$HOME/Development/PX4-Autopilot"
git clone https://github.com/PX4/PX4-Autopilot.git "$GARUDA_PX4_DIR"
(
  cd "$GARUDA_PX4_DIR"
  git checkout 47ce421a88ca870eb8d55eccac5f80fc9b368948
  git submodule update --init --recursive
  python3 -m venv .venv
  . .venv/bin/activate
  bash Tools/setup/ubuntu.sh --no-nuttx --no-sim-tools
  python -m pip install -r Tools/setup/requirements.txt
  make px4_sitl_default
)
test -x "$GARUDA_PX4_DIR/build/px4_sitl_default/bin/px4"
```

Run the pinned upstream setup script in that activated venv; it installs host build packages with sudo and Python build requirements. Gazebo was installed separately above. This builds SITL without launching it. See [PX4 environment documentation](https://docs.px4.io/main/en/dev_setup/dev_env). Changing PX4 revision later also requires rebuilding the ROS message subset below.

### Project-local ROS 2

Download Pixi 0.83.0's `pixi-x86_64-unknown-linux-musl.tar.gz` from its [official release](https://github.com/prefix-dev/pixi/releases/tag/v0.83.0), verify its SHA-256, and extract `pixi` into `.runtime/tools/`:

```bash
mkdir -p .runtime/tools
curl -fL https://github.com/prefix-dev/pixi/releases/download/v0.83.0/pixi-x86_64-unknown-linux-musl.tar.gz \
  -o .runtime/pixi.tar.gz
printf '%s\n' '064f371e692aaa3400ac581c89da2843c91437f20c2d9155395a5d0037f33551  .runtime/pixi.tar.gz' \
  | sha256sum -c -
tar -xzf .runtime/pixi.tar.gz -C .runtime/tools pixi
PIXI_CACHE_DIR="$PWD/.runtime/pixi-cache" .runtime/tools/pixi install --locked
```

Keep `pixi.toml` and `pixi.lock` together. This provides ROS Jazzy, Cyclone DDS and colcon. Do not source an unrelated system ROS environment into these commands.

### XRCE-DDS agent and generated messages

```bash
git clone https://github.com/eProsima/Micro-XRCE-DDS-Agent.git .runtime/agent-src
git -C .runtime/agent-src checkout 73622810d984349b80bbac0ef55fc0b694d62222
cmake -S .runtime/agent-src -B .runtime/agent-build \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PWD/.runtime/agent" \
  -DUAGENT_P2P_PROFILE=OFF -DUAGENT_CED_PROFILE=OFF
cmake --build .runtime/agent-build -j4
cmake --install .runtime/agent-build
python3 hosts/pc/prepare_messages.py "$GARUDA_PX4_DIR" .runtime/matched_ws/src/px4_msgs
.runtime/tools/pixi run colcon build --base-paths .runtime/matched_ws/src \
  --build-base .runtime/matched_ws/build --install-base .runtime/matched_ws/install \
  --merge-install --cmake-args -DBUILD_TESTING=OFF
```

The agent revision is pinned, but its upstream superbuild follows moving Fast DDS/CDR branches. The known build resolved Fast DDS `adc13326bdb4b9df05405102238ea205d1974478` and Fast CDR `757d5e422253568c487ad97c22e44680fc1ddbaf`. Compare resolved source revisions with [versions](../bench/versions.md); complete dependency pinning remains a release task. Do not label an untested changed build equivalent.

`prepare_messages.py` extracts the needed messages from **your PX4 source**, including versioned definitions. A random `px4_msgs` branch can compile but prevent fresh telemetry. The launcher expects `.runtime/matched_ws/install/setup.bash` and `.runtime/agent/bin/MicroXRCEAgent`.

### Vision dependencies and model

```bash
/usr/bin/python3 -m venv --system-site-packages .runtime/vision-env
.runtime/vision-env/bin/python -m pip install -r hosts/pc/vision-requirements.txt
python3 hosts/pc/fetch_vision.py
.runtime/vision-env/bin/python -c 'import cv2, numpy, onnxruntime; from gz.transport13 import Node; import gz.msgs10.image_pb2; print("vision imports OK")'
```

The venv deliberately sees apt's Gazebo Python bindings and protobuf. Do not upgrade protobuf indiscriminately with pip. The recorded Debian protobuf package is 3.21.12; its Python module reports 4.21.12. The requirements pin NumPy 1.26.4, OpenCV 4.10.0.84 and ONNX Runtime 1.23.2 plus their listed dependencies. The YOLOX fetcher verifies the model hash; see [model card](../models/yolox_pc.md).

### Actor asset and QGroundControl

The visual world needs Mingfei's `actor` v1 mesh from Gazebo Fuel (CC BY 4.0). Download using Gazebo Fuel or its browser:

```bash
gz fuel download -u https://fuel.gazebosim.org/1.0/Mingfei/models/actor/1
```

Select the resulting `meshes/walk.dae` in the launcher's setup panel. Its usual location is `~/.gz/fuel/fuel.gazebosim.org/mingfei/models/actor/1/meshes/walk.dae`. The mesh is not committed. The generated world substitutes your path at startup.

Install QGC's desktop dependencies on Ubuntu 24.04:

```bash
sudo apt-get install gstreamer1.0-plugins-bad gstreamer1.0-libav gstreamer1.0-gl \
  libfuse2t64 libxcb-xinerama0 libxkbcommon-x11-0 libxcb-cursor0
```

Download the x86_64 AppImage from the [official QGC installation page](https://docs.qgroundcontrol.com/Stable_V5.0/en/qgc-user-guide/getting_started/download_and_install.html), place it in your Applications directory and make that file executable with `chmod +x`. Record its release and checksum. This lab uses UDP, so serial-port group changes and removing ModemManager are not required for its simulator connection. QGC supplies the heartbeat needed by the current SITL arming configuration. The launcher uses separate QGC settings under `.runtime`.

## 5. Configure and launch

From the GARUDA checkout:

```bash
GARUDA_SIM=1 python3 hosts/pc/lab_launcher.py
```

Open **http://127.0.0.1:8100/**. The original Pi browser simulation is the home view. Switch to **PX4 flight lab**, then open **Setup / paths for this computer**. Save:

| Setting | What to select |
|---|---|
| PX4 | Checkout containing `build/px4_sitl_default/bin/px4` |
| QGC | Executable or AppImage file |
| Actor | Full path to `walk.dae` |
| Replay dataset root | Parent containing the `datasets/visdrone/…` tree described below |
| Pi URL | `http://YOUR_PI_ADDRESS:8098` |

Common folders are searched automatically. Settings are saved to `.runtime/lab-config.json` with keys `px4`, `qgc`, `actor`, `data_root`, `pi_url`. Missing files appear in readiness results. Discovery does not install packages or build dependencies. Saved path changes apply on the next process start, not by restarting an ongoing flight.

Press **Start PX4 lab + Gazebo + QGC**. It starts/reuses local components, without arming. Wait for fresh telemetry and **GROUND / disarmed**. The browser stays in one window; Gazebo and QGC are native desktop applications and have their own windows. Switching back to the original demo does not stop the simulated flight.

| Endpoint | Purpose |
|---|---|
| Pi `:8098/demo` | Original independent fallback |
| PC `127.0.0.1:8100` | Main launcher and both views |
| PC `127.0.0.1:8099` | ROS controller diagnostics |
| PC `127.0.0.1:8101` | Optional CPU vision/replay service |
| UDP 8889 | Isolated XRCE agent |

Isolation uses ROS domain 42, `/garuda_sitl`, Gazebo partition `garuda_isolated`, PX4 instance 3/system ID 4. Keep this demonstration separate from real flight controllers. `GARUDA_SIM=1` is required for flight startup; it is not a network security boundary.

## 6. Present the original Pi demo

1. Open the original view and wait for a moving annotated camera feed. Select a visible person by clicking their box.
2. Adjust follow gap in the UI. The shadow quad uses bounded acceleration, speed and yaw to follow or orbit the selected person. The virtual gimbal is a crop of the camera image.
3. Enable pose/gesture mode to show the selected person's gesture readout. Keep shoulders, elbows and wrists visible; use the gesture vocabulary below.
4. Walk sideways, hide briefly and return. Hold on lost vision is intentional. Appearance matching may recover the person; ambiguous returns need selection/confirmation. A matching outfit is not proof of identity.
5. Optional face matching adds CPU YuNet/SFace descriptors to the original demo. Enable it and enroll the selected person through that view's controls; clear enrollment after the demonstration. Poor lighting, head angle and small faces can prevent matching. It does not provide PX4 face-based authority.
6. Use browser fullscreen for presentation. The compact layout and gesture panel were adjusted for fullscreen, but check the actual display/zoom before visitors arrive.

The map's distance/grid and follow gap are illustrative unless the depth path has been physically calibrated and validated at that booth. No real aircraft is controlled by this view.

## 7. Operate the PX4 lab and gestures

Live Pi camera is the default main feed; Gazebo is a smaller live scene view. Select a **person in the main camera**, then use Takeoff, Follow, Hold, Orbit or Land. Takeoff uses the configured 1.5 m altitude. Gap, speed, travel limit and orbit direction are adjustable. The Pi-camera target position comes from image center/person size; it is schematic booth mapping, not calibrated world localization.

For gestures, enable **Flight gestures** in the PX4 view. This explicitly requests Pi pose mode through `/api/detector?mode=pose`; select yourself again if the mode change clears the target. **Enable pose camera** repairs a mode mismatch. The gesture readout/countdown is below the main camera. Only the selected person's fresh observations authorize a command.

| Pose | Gesture ID | PX4 action |
|---|---|---|
| One arm raised | `follow_me` | Take off / follow |
| Both arms stretched sideways (T-pose) | `circle` | Orbit around the selected target |
| Arms crossed | `land` | Land |
| Both hands raised | `stop` | Hold |

Hold acts immediately; the other commands require 1.2 seconds of sustained fresh observations. Commands are one-shot per gesture onset: lower/change your arms before repeating. Orbit means moving the simulated aircraft around the target, not rotating the icon in place. Disabling flight gestures does not change the Pi detector back; pose mode is shared with the original view. Gazebo actors and recorded footage do not produce these human pose commands.

Loss of a selected track causes Hold. Guarded clothing appearance can recover a new ID within 30 seconds; ambiguous matches require confirmation. A source/session change clears authority. Lost PX4 telemetry is a different fault: the controller stops its offboard heartbeat and the simulator's configured failsafe takes over. Do not interpret stale displayed positions as fresh control.

Land and confirm disarmed before switching camera sources. For **Drone camera / Gazebo**, use the visual world started by the main launcher, take off to obtain a useful view, click a detected actor box, then Follow/Orbit. This path uses actual rendered RGB/depth pixels and CPU detection; it does not take target coordinates from the simulated actor's ground truth.

## 8. Replay drone footage and measure performance

For registered VisDrone SOT clips, set the replay dataset root so the following exists:

```text
YOUR_DATA_ROOT/
  datasets/visdrone/VisDrone2019-SOT-val/
    sequences/uav0000086_00870_s/*.jpg
    annotations/uav0000086_00870_s.txt
```

Use your authorized dataset copy; recordings are not bundled. The GUI catalogs the sequence directories, offers pause/play/rate controls, and a 120-frame report. A blank root currently falls back to the checkout's `datasets` directory, so explicitly setting the parent above avoids an accidental nested-directory mismatch.

For an arbitrary MP4/AVI, first land/disarm and stop the managed vision process so port 8101 is free. Start this in a separate terminal:

```bash
python3 hosts/pc/lab_processes.py stop --only vision
.runtime/vision-env/bin/python hosts/pc/vision.py --video /absolute/path/drone.mp4
```

Choose recorded footage in the lab and select that file. The custom process runs in the foreground; Ctrl-C stops it. To return to managed vision, stop the custom process and run `GARUDA_SIM=1 python3 hosts/pc/lab_processes.py start --only vision`. Starting the whole lab while this custom server owns 8101 may conflict with managed startup.

For an offline report without any aircraft control:

```bash
.runtime/vision-env/bin/python bench/vision_replay.py \
  --clip uav0000086_00870_s --data-root /absolute/path/YOUR_DATA_ROOT \
  --frames 120 --out .runtime/reports/visdrone-new-run.json
# An unannotated video still needs a label via --clip:
.runtime/vision-env/bin/python bench/vision_replay.py \
  --clip my-drone-video --video /absolute/path/drone.mp4 \
  --frames 120 --out .runtime/reports/drone-new-run.json
```

Choose a new output basename for every run; reports refuse to overwrite evidence. The detector processes sequential frames. Playback slower than source FPS is not a real-time pass. Annotated SOT reports give coverage at IoU ≥ 0.5, losses and changes of matched track ID; they are not HOTA/IDF1. Unannotated footage cannot yield ground-truth accuracy. These are PC CPU results; feeding video through the Pi's Hailo pipeline remains a separate planned resource-handover task.

## 9. Shutdown and reconnect recovery

Normally Land, wait for **GROUND / disarmed**, then:

```bash
python3 hosts/pc/lab_processes.py stop
```

Close the dedicated Gazebo and QGC windows, then Ctrl-C the launcher terminal if finished. The Pi stays available independently. `stop` refuses when telemetry reports armed.

Closing owned Gazebo now ends its PX4 session. Pressing Start again creates fresh controller/DDS state. If an interrupted session still shows **PX4 telemetry stale / Hold**, use **Reset simulation · ground aircraft**. This explicitly discards the simulated flight and recreates the isolated session; it is not a gentle reconnection of an airborne aircraft. It clears selection and disables gestures. Wait for fresh GROUND/disarmed telemetry, select the person again and re-enable gestures. It leaves the Pi and QGC intact.

For fallback, simply open `http://YOUR_PI_ADDRESS:8098/demo`. No package rollback is needed. Full archive restoration is only for an actually altered Pi deployment; see [recovery procedure](px4-demo-isolation.md).

## 10. Troubleshooting and acceptance check

| Symptom | Check / action |
|---|---|
| Original view blank | Verify configured Pi URL, Ethernet address, mono service and direct `:8098/demo` access |
| No camera output | Inspect rpicam/V4L2 discovery and journal; avoid another process owning the camera; force the valid USB node if needed |
| CM3 not found on either connector | Power off, test known-good cable then module; the old empty-I2C evidence suggests hardware but is not a completed component swap diagnosis |
| Hailo cannot load model | Match H10 architecture, HEF compilation/runtime, driver/firmware and Python ABI; inspect actual error before changing packages |
| Feed present but no gesture | Enable pose, select the new person ID, show full upper body/wrists, watch the countdown; gestures work only on live Pi source |
| Return selects nobody | Appearance/face evidence may be weak or ambiguous; manually reselect rather than forcing identity |
| Start reports missing files | Complete prerequisite builds/downloads and correct saved paths; the launcher is not an installer |
| PX4 telemetry stale after reopening | Use explicit simulation reset, verify agent/controller logs and matched PX4 messages |
| Takeoff refused | Wait for fresh pose/status and QGC heartbeat; inspect PX4/QGC arming reason before retrying |
| Gazebo view has no detections | Confirm visual world, vision process/model, actor in camera field of view and matching Gazebo bindings |
| Replay list empty | Verify exact dataset-root layout or start vision with `--video` |
| Low FPS / high Pi temperature | Check current throttle flags; fit active cooling; do not report throttled results as performance capability |

Logs: `.runtime/processes/` on PC; `journalctl -u garuda-mono` on Pi. Read process status with `python3 hosts/pc/lab_processes.py status`. Record source/model/package versions with every result.

Before a show, validate direct Pi fallback, both views, selection and loss/return, each gesture with two people visible, adjustable gap, a complete orbit in both directions, Land/disarm, close/reopen recovery and a sustained cooled run. Record failures honestly. These checks include work not yet fully certified by the existing logs.

Developer tests use pytest (install it in a separate developer environment if absent):

```bash
PYTHONPATH=hosts/pc python3 -m pytest services/garuda-follow/tests hosts/pc/test_lab_launcher.py -q
.runtime/vision-env/bin/python -m pytest hosts/pc/test_vision.py -q
```

The vision environment may need pytest installed explicitly for development; it is not a runtime requirement. Simulator smoke harnesses `bench/sitl_smoke.py` and `bench/visual_sitl_smoke.py` **arm and move SITL**. Run them only in a dedicated disarmed simulation session without concurrent UI commands, with `GARUDA_SIM=1`. Consult [bench evidence](../bench/benchlog.md) before claiming a test passed.
