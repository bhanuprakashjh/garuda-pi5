# CamArray bring-up on Trixie via capture-and-replay (Plan B)

The Arducam CamArray HAT's bridge is a **Pivariety-style ArduChip** (16-bit register
protocol) sitting at **i2c `0x51` on bus 0** (the VideoCore i2c — Pi 40-pin **pins 27/28**,
GPIO0/1), powered over the HAT's **USB input** (the CSI ribbon + berg SDA/SCL alone don't
power it). Confirmed on-bench 2026-08-01: chip ID `0x82 0x47 0x03` at `0x51`.

The stock `ov9281` driver can't drive it (8-bit sensor protocol vs 16-bit ArduChip), and
Arducam's installer is broken on Trixie 2026 (see `docs/decisions.md`). **Plan B**: capture
the exact ArduChip init that Arducam's *working* stack emits (on a Bookworm card), then
**replay it on Trixie** via `i2ctransfer` + raw V4L2 capture — **no libcamera swap, CM3-safe.**

Wiring stays identical throughout (do CSI/GPIO work with the Pi OFF):
- HAT **USB** → 5 V power.
- HAT **SDA/SCL/GND** → Pi **pin 27 / pin 28 / GND** (bus 0, i2c_vc).
- Camera CSI ribbon → Pi **CAM1**.

---

## Part 1 — CAPTURE (on a spare Raspberry Pi OS **Bookworm** SD card)

Swap in a Bookworm card (Pi off). The Trixie card is untouched — this is isolated.

1. **Install Arducam's CamArray stack** (works on Bookworm) per their quick start:
   ```
   wget -O ip.sh https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases/download/install_script/install_pivariety_pkgs.sh
   chmod +x ip.sh
   ./ip.sh -p libcamera_dev
   ./ip.sh -p libcamera_apps
   ```
   Add the CamArray overlay to `/boot/firmware/config.txt` per Arducam's quick start
   (`camera_auto_detect=0` + the CamArray `dtoverlay`), enable i2c (`./enable_i2c_vc.sh`),
   reboot. Confirm `rpicam-hello --list-cameras` shows the combined camera.

2. **Turn on kernel i2c tracing, then bring the camera up** (the driver inits the ArduChip):
   ```
   sudo apt install -y i2c-tools
   sudo bash -c 'echo 1 > /sys/kernel/debug/tracing/events/i2c/enable'
   sudo bash -c ': > /sys/kernel/debug/tracing/trace'        # clear the buffer
   rpicam-hello -t 2000 --nopreview                          # triggers the init
   sudo cat /sys/kernel/debug/tracing/trace > ~/camarray_i2c_trace.txt
   sudo bash -c 'echo 0 > /sys/kernel/debug/tracing/events/i2c/enable'
   ```

3. **Record the stream geometry** (the replay side needs the exact format the ArduChip is set to):
   ```
   rpicam-hello --list-cameras                               # note WxH + pixel format
   media-ctl -p 2>/dev/null | tee ~/camarray_media.txt       # CSI topology + fmt
   dmesg | grep -iE 'ov9281|arducam|cfe|link rate' > ~/camarray_dmesg.txt
   grep -nE 'dtoverlay|camera_auto_detect|i2c' /boot/firmware/config.txt > ~/camarray_config.txt
   ```

4. **Bring back**: `camarray_i2c_trace.txt`, `camarray_media.txt`, `camarray_dmesg.txt`,
   `camarray_config.txt`. The trace lines look like
   `i2c_write ... addr=0x51 ... [NN] data=...` / `i2c_reply ...` — that IS the init sequence.

> If tracing is empty, the driver may use `/dev/i2c` from userspace instead — capture with
> `sudo strace -f -e ioctl -s 256 rpicam-hello -t 2000 2>strace.txt` and grab the
> `I2C_RDWR` payloads, or run `sudo i2cdump`-style logging. Bring whatever you get.

---

## Part 2 — REPLAY (back on the Trixie bench)

1. Feed the captured trace to the parser/replayer:
   ```
   sudo python3 hosts/pi5/camarray_replay.py --trace camarray_i2c_trace.txt --bus 0 --addr 0x51
   ```
   It extracts the ordered `(reg16, value)` writes to `0x51` and replays them via
   `i2ctransfer` (16-bit register addressing) to enable + combine the cameras.

2. With the ArduChip streaming the combined MIPI frame, configure the RP1 CSI receiver to
   the captured geometry (`camarray_media.txt`) with `media-ctl`/`v4l2-ctl` and capture raw
   frames via V4L2 — fed straight into `services/garuda-camera` (`CamArrayBackend` does raw
   V4L2, no libcamera). Split → synced stereo views → depth, per that service.

3. If the raw path is fiddly, the fallback is the arducam-pivariety in-kernel driver pointed
   at `0x51`/bus 0 — but the replay path keeps the stock CM3 libcamera stack untouched.

**Net:** we never swap libcamera on the Trixie bench; the ArduChip is configured by replaying
Arducam's own (captured) sequence, and video is captured raw. CM3/portal/follow stay pristine.
