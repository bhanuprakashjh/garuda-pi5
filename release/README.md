# GARUDA testing release: install, check, launch

This is **v0.2.0-test.1**, an early testing distribution. It packages the current
source, a Linux x86_64 installer executable, dependency/model manifests, launch
script, checksums, license notices and a small generated replay clip. It is an
**online installer kit**, not an offline image or a single executable containing
Gazebo, ROS, PX4 and the Pi accelerator driver.

Supported installer targets:

- **PC:** Ubuntu 24.04 x86_64. The binary is built on Ubuntu 24.04/Python 3.12;
  older glibc distributions, Windows, macOS and ARM PCs are not supported.
- **Pi:** Raspberry Pi OS Trixie 64-bit, Python 3.13, user `garuda`, source at
  `/home/garuda/garuda-sdk-deploy`. Use the Python installer here; the x86 binary
  cannot run on the Pi. Hailo-10H firmware/runtime/driver/binding must match 5.3.0.

No installer operation starts a service, opens a camera, arms a vehicle or changes
the existing Pi remotely. Preview is the default; `--apply` explicitly installs.
Run as your normal user, not root; only the host package stage invokes sudo.
Use a new directory/card for testing, not the working booth deployment.

## Download and verify

From the [GitHub testing release](https://github.com/bhanuprakashjh/garuda-pi5/releases/tag/v0.2.0-test.1),
download `garuda-demo-v0.2.0-test.1.tar.gz` and `SHA256SUMS`. The tarball already
contains the executable; the separate binary download is optional.

```bash
# Check just the downloaded kit against the published checksum list:
grep '  garuda-demo-v0.2.0-test.1.tar.gz$' SHA256SUMS | sha256sum -c -
tar -xzf garuda-demo-v0.2.0-test.1.tar.gz
cd garuda-demo-v0.2.0-test.1
sha256sum -c CONTENTS.sha256
```

The standalone `garuda-setup-linux-x86_64` asset is the installer, not the flight
application. It still needs this extracted source kit. If downloaded separately,
make it executable and pass `--root /absolute/path/to/extracted-kit`. Runtime
license notices are both inside the kit and in `binary-licenses.tar.gz`.

## PC: automated dependency setup

Preview first, then install:

```bash
./garuda-setup-linux-x86_64 install pc
./garuda-setup-linux-x86_64 install pc --apply
./garuda-setup-linux-x86_64 doctor pc
./release/start-demo.sh
```

Source users can replace the executable with `python3 release/setup.py`.
Open **http://127.0.0.1:8100/**, set your Pi URL under Setup / paths, then use
**Start PX4 lab + Gazebo + QGC**. Wait for fresh GROUND/disarmed telemetry before
flight commands. The launcher starts at the original Pi demo; that view needs an
accessible Pi. For PC-only testing, switch to PX4 and select Drone camera/Gazebo
or Recorded footage. Native Gazebo/QGC have their own desktop windows.

The installer performs these stages in order:

| Stage | Installs/builds |
|---|---|
| `system` | Explicit apt build dependencies, OSRF Harmonic repository, Gazebo, Python transport13/msgs10/protobuf, QGC desktop libraries |
| `px4` | Pinned PX4 source/submodules in `.runtime/PX4-Autopilot`, private build venv, SITL executable; no system pip modification |
| `ros` | Checksum-pinned Pixi, locked RoboStack Jazzy, pinned XRCE agent plus Fast DDS/CDR revisions, matching PX4 messages |
| `vision` | Private venv with system Gazebo bindings, pinned CPU vision requirements, verified YOLOX model, generated sample clip |
| `assets` | Checksum-pinned QGC 5.0.8 from upstream, Mingfei actor v1 from Fuel |

Builds use two jobs by default; set `--jobs N` if appropriate. Downloads and builds
need significant disk space and internet access. Apt package patch versions and
PX4's Python build requirements are resolved at installation time; their inventory
is recorded rather than described as an immutable offline environment.

Rerun a failed stage after correcting its error, for example:

```bash
./garuda-setup-linux-x86_64 install pc --stage ros --apply
```

Rerun `install pc --apply` to finish all stages/configuration. Existing correct
checkouts and verified downloads are reused. Different/dirty PX4 source checkouts
are refused instead of reset. Existing launcher settings are preserved. Components
are installed under `.runtime`; do not move this directory after building because
venvs/CMake/ROS install prefixes contain absolute paths. Download/extract the kit
to its final directory first. Rebuild after relocation.

The system stage installs packages and the OSRF apt source; it does not run
`apt upgrade`, remove ModemManager or alter serial permissions. Review the printed
plan before applying on another computer. Existing custom PX4/QGC installations
can still be used through the GUI; `doctor pc` reports the installer-managed paths.

## Pi: packages, known-good models and checks

On a **new Pi deployment**, unpack source to `/home/garuda/garuda-sdk-deploy` as
user `garuda`. The x86 executable in the kit is unused on the Pi.

```bash
cd /home/garuda/garuda-sdk-deploy
python3 release/setup.py install pi
python3 release/setup.py install pi --apply --fetch-pi-models --faces
python3 release/setup.py doctor pi
```

This installs camera/OpenCV/NumPy utilities and optionally fetches face models and
three exact evaluation HEFs directly from upstream. It does not redistribute
those models in the release archive. Observe their upstream licenses and project
bench/evaluation restrictions. The model download flag is explicit because these
are evaluation assets, not product-cleared dependencies.

Each download is checked against hashes read from the preserved working baseline
`99db1c5`, then independently matched against the upstream bytes. Primary YOLO26m
and DepthAnything use v5.3.0 URLs. The **exact working pose HEF** is served at the
v5.4.0 URL; it matches the archived pose hash, while the v5.3.0 pose file differs.
That specific byte match is the basis for this pin, not a claim that arbitrary
v5.4.0 models work with 5.3.0. The old broad `models/fetch.sh` remains unsuitable as
a general installer.

Two prerequisites still require operator-provided artifacts:

1. **Hailo-10H 5.3.0 driver/runtime and Python 3.13 ARM64 binding.** Obtain the
   matched vendor packages through Hailo's supported distribution and follow
   its kernel-specific instructions. The installer intentionally does not mix
   distribution packages, add stray wheels, patch boot configuration or replace
   drivers on an existing Pi. `doctor pi` checks identity/firmware and imports.
2. **Custom YOLO26-depth HEF and its `metadata.yaml`.** Build using
   `models/build_yolo26_depth_hef.sh` with the vendor DFC, or obtain the authorized
   known-good bench artifacts. The release does not rehost these evaluation assets.
   Their hashes and paths are listed in `release/dependencies.json`.

Import a model directory (same layout as `models/`; it may contain only the
remaining custom-depth directory after the other downloads):

```bash
python3 release/setup.py install pi --apply --fetch-pi-models \
  --model-dir /absolute/path/to/authorized-models --faces
```

All required artifacts must match the manifest before copying. A different custom
build needs its own reviewed manifest/validation; do not disable checksum checks
to hide the difference. Both depth HEFs are currently required by application
startup. Failed/missing readiness checks return exit code 2 and are **not** a
successful complete installation. No service is installed or started automatically.

After `doctor pi` passes, follow **DEMO_USER_GUIDE §3, Start and install the mono
service** for the foreground camera test and systemd unit. Add the user to `video`
if camera permissions require it, then log out/in. Camera Module 3 needs a correct
Pi 5 CSI cable and powered-off attachment. `rpicam-hello --list-cameras` must see
it; USB remains supported. Actual camera/HEF loading is an operator test, not a
side effect of `doctor`.

Optional open-vocabulary prompts are **outside this install profile**. They also
need a compatible Pi-side ONNX Runtime, `tokenizers`, and the CLIP ONNX/tokenizer
assets described in `models/yolo_world_v2s_h10.md`. PC ONNX Runtime installation
does not satisfy the Pi requirement. The kit excludes the legacy tracked world
HEF along with other HEFs; ordinary person detection and gestures do not need it.

## Sample footage and replay

The kit includes `samples/playback-check.avi` and automatically lists it as
**Synthetic playback check (not a detection benchmark)** in Recorded footage.
This is a six-second generated animation, useful for checking playback and report
creation. It is not aerial footage; no person detections are promised. It uses
our BSD-licensed generator and does not republish VisDrone or private recordings.

For real performance measurements use your own authorized video, or an authorized
VisDrone SOT dataset with the guide's folder layout. The UI disables flight for
replay. See `samples/README.md` and DEMO_USER_GUIDE §8 for custom-video startup.

## What this prerelease proves

Installer unit tests exercise no-op preview, checksum failure/cache handling,
platform checks, preservation of existing settings/source, missing-prerequisite
failure and Pi-path guard. Release build checks cover the executable, archive
content/hashes and sample decoding. Existing follow/launcher and vision tests
are run; the PC vision stage is exercised in a fresh project-local venv on the
existing Ubuntu machine. Detailed results are in `release/VALIDATION.md`.

**Not certified:** a complete fresh-PC or fresh-Pi installation, new QGC 5.0.8
flight behavior, a complete live orbit, and sustained operation with active cooling.
This release does not change those gates. The Pi was unreachable during packaging;
model fingerprints came from its preserved archive, not a new hardware test.

## Build the release yourself

```bash
python3 -m venv .runtime/release-build
.runtime/release-build/bin/pip install -r release/build-requirements.txt
# Run the vision stage first to provide the sample generator's OpenCV/NumPy:
python3 release/setup.py install pc --stage vision --apply
.runtime/release-build/bin/python release/build.py \
  --vision-python .runtime/vision-env/bin/python
```

Build on Ubuntu 24.04 x86_64. Outputs are in `.runtime/release-dist`. Only tracked
source files are included, excluding HEFs/ONNX/video artifacts; the generated
sample and installer binary are added explicitly. Do not publish from a dirty
checkout; source revision and per-file hashes are included. Dependencies are
fetched by the recipient from their providers, not copied out of our live bench.

PyInstaller is a build tool under GPL-2.0 with a bootloader distribution exception;
see its [official license](https://www.pyinstaller.org/en/stable/license.html).
Our installer remains BSD-3-Clause. The binary includes CPython and associated
runtime libraries with notices in `release/binary-licenses`; the flight application
source and its existing dependency licenses remain visible in the source kit.
