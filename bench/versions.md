# bench/versions.md — environment snapshots

Rule: append a new dated snapshot after every upgrade or system change on the Pi.
All entries are from commands actually run on the bench Pi (or PC where noted).

---

## 2026-07-29 — Trixie first boot + Hailo h10 stack (CURRENT baseline)

Supersedes the Bookworm entry below (re-flash same day; see docs/decisions.md). Access unchanged: `garuda@10.42.0.128` via PC `eno1` shared network.

- **Image**: `2026-06-18-raspios-trixie-arm64-lite.img.xz` (raspios_lite_arm64 mainline), sha256 verified on PC before flash.
- **uname -a**: `Linux raspberrypi 6.18.34+rpt-rpi-2712 #1 SMP PREEMPT Debian 1:6.18.34-1+rpt1 (2026-06-09) aarch64 GNU/Linux`
- **OS**: Debian GNU/Linux 13 (trixie), 64-bit Lite
- **Python**: 3.13.5
- **Hailo packages** (all from archive.raspberrypi.com trixie/main):
  - `hailo-h10-all` 5.1.1, `h10-hailort` 5.1.1, `h10-hailort-pcie-driver` 5.1.1, `python3-h10-hailort` 5.1.1-1
  - `hailo-tappas-core` 5.1.0, `python3-hailo-tappas` 5.1.0, `hailo-models` 1.0.0-2, `rpicam-apps-hailo-postprocess` 1.12.0-1
  - kernel module `hailo1x_pci` loaded without reboot
- **hailo-gen-ai-model-zoo** 5.1.1 (deb from dev-public.hailo.ai, not apt; provides `hailo-ollama`; installed same day for P1 LLM item; Hailo's own licence — bench tool only, never linked into services)
- **linuxptp** 4.2-1+b1 (Pi; GPLv2 — external daemon only per licence policy, never linked). PC side: linuxptp 4.0-1ubuntu1.1
- **docker.io** 26.1.5+dfsg1 (Pi, Debian trixie repo, Apache-2.0; installed 2026-07-30, daemon enabled, `garuda` in docker group)
- **garuda/ros-jazzy** image (sha256:a132a6c1f783…, supersedes e24d6100) from `hosts/pi5/Dockerfile.ros-jazzy`: ros:jazzy-ros-base + rmw-cyclonedds-cpp + demo-nodes-cpp + **px4_msgs @ c0f921bce0d76f83ca6d3ccd438adbee00a09f3a** (BSD-3) + **Micro-XRCE-DDS-Agent @ 155cfaaf8b7abac2e85d4a62d3649b09ace0be55** (Apache-2.0), `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` — no GPLv3
- **PX4-Autopilot** (PC, SITL only): shallow clone @ 47ce421 ("fix(simulator_sih): restore xvert front transition (#28090)"), built `px4_sitl` with venv python deps; BSD-3. Runs SIH quad (`sihsim_quadx`), uXRCE client → 10.42.0.128:8888 via `PX4_PARAM_UXRCE_DDS_AG_IP=170524800`
- **Basalt** (Pi, source build 2026-07-30): mateosss fork @ df6e970c, GCC 14.2.0, release + cortex-a76 tuning, `-j2`; deps via fork's apt list (tbb/glew/boost/fmt/yaml-cpp/sqlite3 dev pkgs). BSD-3. Binaries in `/home/garuda/basalt/build/`
- **`hailortcli fw-control identify`**: Control Protocol 2, **Firmware 5.1.1 (release,app), Device Architecture HAILO10H** — PASS
- **vcgencmd get_throttled**: `0x0` (idle; power source not yet verified as 5 V/5 A — re-check under load)
- **ethtool -T eth0**: hardware-transmit/receive, hardware-raw-clock, provider qualifier "Precise (IEEE 1588 quality)", TX modes off/on/**onestep-sync**
- **apt state**: 59 packages upgradable at install time — deliberately NOT upgraded (CLAUDE.md rule 11)
- **sudo**: Trixie no longer grants first-user NOPASSWD; restored via `/etc/sudoers.d/010-garuda-nopasswd`
- **2026-07-30 addendum**: `python3-opencv` 4.10.0+dfsg-5 arm64 (already present via tappas/rpicam-apps-opencv-postprocess deps; now also declared explicitly in `hosts/pi5/install.sh`'s apt list) — `dpkg -l python3-opencv` on the Pi. `garuda-portal.service` + `garuda-configd.service` deployed to `/opt/garuda/` and `systemctl enable --now`'d (both `active`, `enabled`); transient bench unit `garuda-cam-portal` stopped/disabled, portal now owns `:8080`.

---

## 2026-07-29 — first boot after imaging (baseline)

Access: PC `eno1` shared network (NetworkManager profile `pi-share`), Pi at 10.42.0.128, user `garuda`.

- **Image**: `2026-06-18-raspios-bookworm-arm64-lite.img.xz` (raspios_oldstable channel — mainline moved to Trixie 2025-10; Bookworm is oldstable now), sha256 `df4c6fb0…f8f` verified on PC before flash.
- **Model**: Raspberry Pi 5 Model B Rev 1.1
- **uname -a**: `Linux raspberrypi 6.12.93+rpt-rpi-2712 #1 SMP PREEMPT Debian 1:6.12.93-1+rpt1 (2026-06-12) aarch64 GNU/Linux`
- **OS**: Debian GNU/Linux 12 (bookworm), 64-bit Lite
- **vcgencmd get_throttled**: `0x0` (idle, immediately after first boot — under-load check still TBD-bench)
- **lspci**:
  - `0001:01:00.0 Co-processor: Hailo Technologies Ltd. Device 45c4 (rev 01)` — Hailo module enumerates; identity via `hailortcli` pending driver install
  - `0002:01:00.0 Ethernet controller: Raspberry Pi Ltd RP1 PCIe 2.0 South Bridge`
- **Storage**: rootfs `/dev/mmcblk0p2` 235G, 2.0G used after first boot
- **RAM**: 7.9 Gi total
- **Hailo packages**: not yet installed
- **ethtool**: not installed yet (`ethtool -T eth0` pending — needed before PTP work)
- **Power**: source at first boot not yet the verified 5 V/5 A supply — verify before any benchmark and re-check `get_throttled` after load


## 2026-10-31 — Branded demo validation environment

Pi: Linux raspberrypi 6.18.34+rpt-rpi-2712 #1 SMP PREEMPT Debian
1:6.18.34-1+rpt1 (2026-06-09), aarch64; Python 3.13.5. USB V4L2 /dev/video0,
reported 1280x720. Existing Hailo-10H models/installation unchanged, detector primary,
depth y26d. Observed throttled=0xe0008. garuda-mono :8098 checked in local Chrome.
Host controller tests: Node v20.19.5. No throughput gate run or claimed.

## 2026-10-31 — Optional demo face models

Existing Pi OpenCV 4.10.0 provides YuNet and SFace APIs. Detection/pose remain on Hailo-10H;
face descriptors run on Pi CPU in a bounded worker. Model sources pinned to OpenCV Zoo
48b10fa505aeb51fbd127fc60a5c660040e823b7; SHA-256 values in models/fetch_faces.py.
YuNet: MIT, 232589 bytes; SFace: Apache-2.0, 38696353 bytes. Installed under
/home/garuda/garuda-sdk-deploy/models/faces/. License texts and unbuilt Yocto recipe included.
No runtime package upgrades; same OS/Hailo environment as above.

## 2026-11-01 — Isolated PC simulator

- PC Ubuntu 24.04.4 LTS; existing Gazebo Harmonic 8.10.0.
- Existing PX4 checkout/build: 47ce421a88ca870eb8d55eccac5f80fc9b368948.
- ROS 2 Jazzy via project-local RoboStack/Pixi; exact packages in pixi.lock.
- Pixi 0.83.0 release archive SHA-256:
  064f371e692aaa3400ac581c89da2843c91437f20c2d9155395a5d0037f33551.
- Micro XRCE-DDS Agent v2.4.3: 73622810d984349b80bbac0ef55fc0b694d62222.
- Agent superbuild Fast DDS: adc13326bdb4b9df05405102238ea205d1974478;
  Fast CDR: 757d5e422253568c487ad97c22e44680fc1ddbaf.
- ROS message subset generated from the exact local PX4 checkout.
- Existing QGroundControl AppImage supplies GCS heartbeat with isolated settings.
- Pi runtime and baseline worktree unchanged by this integration.

### Visual/replay extension, same date

- Local system-Python venv: Python 3.12.3, NumPy 1.26.4, OpenCV 4.10.0
  (opencv-python-headless 4.10.0.84), Pillow 11.3.0, ONNX Runtime 1.23.2.
- YOLOX-S OpenCV Zoo revision 48b10fa505aeb51fbd127fc60a5c660040e823b7,
  model SHA-256 c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063.
- Hailo-15 camera/world reuse from branch revision b57dd5e; existing Mingfei
  actor v1 in local Fuel cache, CC BY 4.0. No external actor mesh committed.
- Existing system Gazebo transport13 / msgs10 Python bindings and protobuf
  4.21.12; no system package or board package upgrades.
- Model/asset provenance and dependency licences: models/yolox_pc.md.
- Dataset: existing hailo-export VisDrone2019-SOT-val; source decoded-pixel
  SHA-256 and model SHA-256 appear in each replay report.
