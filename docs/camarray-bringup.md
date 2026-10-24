# Arducam CamArray (OV9281 ×4, B0267) bring-up — Pi 5

> **STATUS 2026-08-01 — ✅ WORKING ALL-TRIXIE (raw V4L2), CM3 untouched.**
> The stock in-kernel `arducam-pivariety` driver drives the ArduChip — no Bookworm,
> no Arducam installer, no libcamera swap. Captured a real 5120×800 Y8 combined
> frame (tiles 0,1 = the connected stereo pair, live image data). Recipe:
>
> 1. **Hardware:** HAT on its **USB power** input (CSI ribbon can't power the FPGA
>    bridge); **SDA/SCL berg → Pi i2c**; **CSI ribbon fully seated on CAM1** (a loose
>    ribbon leaves the ArduChip's i2c dark).
> 2. **`/boot/firmware/config.txt`:** `camera_auto_detect=0`, `dtparam=i2c_vc=on`,
>    `dtoverlay=arducam-pivariety,cam0` (CamArray: CSI0 + i2c_vc, ArduChip @ 0x0c),
>    `dtoverlay=imx708,cam1` (keeps the CM3). Reboot.
> 3. **Confirm:** `dmesg | grep pivariety` → "firmware version" + "Using sensor
>    arducam-pivariety … for capture". (libcamera won't list it — stock libcamera
>    has no pivariety CamHelper — that's fine, we capture raw.)
> 4. **Pipeline + capture:** run **`hosts/pi5/camarray_setup.sh`** (disables the ISP
>    branch, enables the raw `csi2→ch0` link, sets `Y8/5120×800`), then
>    `v4l2-ctl -d /dev/video0 --stream-mmap --stream-to=cap.raw`.
> 5. **Split:** `services/garuda-camera` `plan_split(5120, 800, 1, 4)` → `views[0]`,
>    `views[1]` are the stereo pair → `StereoDepthSource`.
>
> Notes: CM3=`cam1`, CamArray=`cam0` (reverse of the silkscreen). See `docs/decisions.md`
> 2026-08-01. The Bookworm/capture-replay fallback (below) is no longer needed.

Global-shutter, hardware-synchronized quad on the CamArray HAT → one combined MIPI
stream. Unblocks **P2 sync capture** and the **P4 stereo** input, and doubles as a
**passive-stereo depth** source (metric range for follow). Software scaffold is in
`services/garuda-camera/` (split → synced views → depth) + `hosts/pi5/camarray_backend.py`.

> Researched 2026-08-01 (sources in the git log of the scaffold commit). **Do NOT
> run Arducam's blanket install script blindly** — it can replace libcamera
> system-wide and break the working CM3 path (CLAUDE.md rule 3). Take the minimal,
> reversible route below and verify coexistence before any boot-config change.

## 0. Before touching hardware
- [ ] **Back up the working boot config:** `sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.pre-camarray`
- [ ] Record current state: `uname -a`, `rpicam-hello --list-cameras`, `dpkg -l | grep -i libcamera` → `bench/versions.md`.
- [ ] Note the CM3 works today on CAM0 (imx708). Goal: **CamArray on CAM1, CM3 untouched on CAM0.**

## 1. Physical (CSI is NOT hot-pluggable — rule)
- [ ] **Power the Pi fully off.** Connect the CamArray HAT ribbon to **CAM1**. Gold-contact orientation per CLAUDE.md.
- [ ] No PCIe conflict with the Hailo AI HAT+ (CamArray is CSI, Hailo is PCIe).

## 2. Minimal enable (stock libcamera first — no Arducam install script)
- [ ] Edit `/boot/firmware/config.txt` (check-then-modify, idempotent): set `camera_auto_detect=0` and add the CamArray overlay per Arducam's quick-start **for this exact HAT/sensor** (the kit's OV9281 are the *special* CamArray version — NOT a plain `dtoverlay=ov9281`). Keep the CM3's `dtoverlay=imx708,cam0` line.
- [ ] Reboot. `rpicam-hello --list-cameras` → **expect BOTH** the CamArray (one wide camera on CAM1) **and** imx708 (CAM0).
- [ ] **Pi 5 PiSP tuning gap:** if you hit `toPiSPImageFormat() … <INVALID>` / black screen, apply the workaround:
      `sudo cp /usr/share/libcamera/ipa/rpi/pisp/uncalibrated.json /usr/share/libcamera/ipa/rpi/pisp/ov9281_mono.json`
      or run with `--tuning-file=uncalibrated.json`.
- [ ] First light: `rpicam-hello -t 3000` on the CamArray camera → confirm the combined frame shows the tiled sub-cameras.

## 3. Coexistence gate (unproven upstream — verify explicitly)
- [ ] CM3 still works: `rpicam-hello --camera <imx708-idx> -t 2000`.
- [ ] CamArray works in the same boot. If they can't coexist, decide: separate boots, or CamArray-only for the VIO workstream. Log the outcome in `docs/decisions.md`.
- [ ] If it all works: this is the moment `config.txt` changed → wait for explicit go-ahead before making it the committed `hosts/pi5/` fragment (rule 10).

## 4. Wire the capture plugin
- [x] **DONE** `hosts/pi5/camarray_backend.py` — real `ICaptureBackend` via **raw V4L2** (not
      picamera2/libcamera: stock libcamera has no pivariety CamHelper). `configure()` runs
      `camarray_setup.sh` and learns the capture node; `start()` spawns
      `v4l2-ctl --stream-mmap --stream-to=-` and a reader thread slices fixed-size frames →
      `on_frame(FrameMeta, memoryview)`. Combined size is **5120×800** (quad HAT, 1×4 tiles).
      Verified live 2026-08-01: 4 frames × 4,096,000 B; runs as `garuda` (video group, no sudo).
- [x] **Tile layout confirmed 1×4:** `plan_split(5120, 800, 1, 4)` → tiles 0,1 carry image
      (mean 36.2, max 255 — the connected stereo pair), tiles 2,3 all-zero (unpopulated). So
      `CamArraySource` uses `views[0]`, `views[1]`.

## 5. Sync verification (P2 gate)
- [ ] LED-flash rig per CLAUDE.md: prove **inter-camera jitter < 100 µs**. Sync is hardware-level on the HAT, but *measure it* before ticking P2. Record in `bench/benchlog.md`.
- [ ] 1 h no-drop at 30 fps run.

## 6. Stereo depth + VIO
- [ ] Stereo-calibrate the chosen pair (Kalibr / OpenCV → `calib/`): `focal_px`, `baseline_mm`, rectification maps.
- [ ] Feed the rectified pair to `StereoDepthSource(focal_px, baseline_mm)` → depth (mm) → obstacle/landing + **metric follow range** (fuse with the Hailo bbox).
- [ ] VIO (Basalt, `IFrontEnd`): stereo pair + **IMU** — the IMU comes from the FC over DDS, so full VIO waits on the CA90 FC; stereo-only odometry/depth can start now.

## 7. Licence + product-tier (rule 6)
- [ ] Verify the Arducam driver/tools licence **before** adding any package (libcamera is LGPL = OK; check Arducam userland). GPLv3 = hard stop.
- [ ] Drop a skeleton bitbake recipe in `yocto/meta-garuda/` for the CamArray driver once the dependency is pinned.
