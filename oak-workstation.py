#!/usr/bin/env python3
"""Linux workstation dialogs for the Chennai OAK camera and handheld mapping kit."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
HANDOUTS = ROOT / "hosts/pi5/demo/chennai"
BENCH = ROOT / "bench"


class Cancelled(Exception):
    pass


def dialog(kind, title, *options):
    result = subprocess.run(["zenity", "--" + kind, "--title", title, "--width", "620",
                             *map(str, options)], capture_output=True, text=True)
    if result.returncode == 1:
        raise Cancelled()
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Desktop dialog failed")
    return result.stdout.rstrip("\n")


def info(title, message):
    dialog("info", title, "--no-markup", "--text", message)


def choose(title, options):
    return dialog("list", title, "--height", "420", "--column", "Choose an action", *options)


def folder(title, initial=None):
    args = ["--directory"]
    if initial:
        args.extend(["--filename", str(initial) + "/"])
    return Path(dialog("file-selection", title, *args)).resolve()


def open_item(item):
    subprocess.run(["xdg-open", str(item)], check=True, stdout=subprocess.DEVNULL)


def measured_grid(tag_mm, gap_mm):
    if not all(math.isfinite(v) and v > 0 for v in (tag_mm, gap_mm)) or gap_mm >= tag_mm:
        raise ValueError("Enter the measured black tag side and smaller white gap, both in millimetres.")
    return {"tagCols": 6, "tagRows": 6, "tagSize": tag_mm / 1000, "tagSpacing": gap_mm / tag_mm}


def tools_environment(release, needed):
    release = Path(release)
    env = dict(os.environ)
    bins = release / "bin"
    for name in needed:
        if not os.access(bins / name, os.X_OK):
            raise ValueError(f"Choose the unpacked Basalt release folder containing bin/{name}.")
    env["LD_LIBRARY_PATH"] = str(release / "lib") + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    return env


def check_take(take):
    for name in ("frames.jsonl", "imu.jsonl", "calibration.json", "session.json"):
        if not (take / name).is_file():
            raise ValueError(f"Choose a complete recorder folder. Missing {name} in {take}.")
    if json.loads((take / "session.json").read_text()).get("complete") is False:
        raise ValueError("This take is marked incomplete. Select a complete recording.")


def run(command, work, env, title):
    with (work / "operator.log").open("a") as log:
        log.write(f"\n{title}\n")
        log.flush()
        with subprocess.Popen(["zenity", "--progress", "--pulsate", "--auto-close",
                               "--no-cancel", "--title", title, "--text",
                               "Keep this window open. Details are written to operator.log."],
                              stdin=subprocess.PIPE, stdout=subprocess.DEVNULL) as indicator:
            try:
                result = subprocess.run(list(map(str, command)), cwd=work, env=env,
                                        stdout=log, stderr=subprocess.STDOUT)
            finally:
                indicator.stdin.close()
    if result.returncode:
        raise RuntimeError(f"{title} failed. See {work / 'operator.log'}.")


def check_solution(path):
    if not path.is_file():
        raise ValueError("Basalt closed without saving calibration.json. Use save_calib before closing.")

    def finite(value):
        if isinstance(value, dict):
            return all(finite(v) for v in value.values())
        if isinstance(value, list):
            return all(finite(v) for v in value)
        return not isinstance(value, float) or math.isfinite(value)
    doc = json.loads(path.read_text())["value0"]
    if not finite(doc) or len(doc["intrinsics"]) != 2 or len(doc["T_imu_cam"]) != 2:
        raise ValueError("The saved solution has invalid values or lacks both camera models and poses.")
    for camera, pose in zip(doc["intrinsics"], doc["T_imu_cam"]):
        intrinsics = camera["intrinsics"]
        if (not all(math.isfinite(float(v)) for v in intrinsics.values())
                or min(float(intrinsics["fx"]), float(intrinsics["fy"])) <= 0):
            raise ValueError("Stereo fit did not produce usable focal lengths or distortion parameters.")
        if not all(math.isfinite(float(pose[k])) for k in ("px", "py", "pz", "qx", "qy", "qz", "qw")):
            raise ValueError("The saved camera pose has invalid values.")
        quaternion_norm = math.sqrt(sum(float(pose[k]) ** 2 for k in ("qx", "qy", "qz", "qw")))
        if not math.isclose(quaternion_norm, 1.0, abs_tol=1e-4):
            raise ValueError("The saved camera pose has an invalid rotation.")


def bench_host():
    host = dialog("entry", "Bench Pi address", "--text", "Enter the Pi hostname or IPv4 address.")
    url = urlsplit("http://" + host)
    if not url.hostname or url.netloc != url.hostname or url.path or url.query or url.fragment:
        raise ValueError("Enter just a hostname or IPv4 address, without a port or path.")
    return url.hostname



def bench_check(host):
    def snapshot():
        values = {}
        for key, route in (("camera", "8096/health"), ("detector", "8098/api/state")):
            try:
                with urlopen(f"http://{host}:{route}", timeout=4) as response:
                    data = json.load(response)
                if key == "camera":
                    values[key] = {k: data.get(k) for k in ("epoch", "raw_seq", "depth_backend", "error", "depth_error")}
                    values[key]["video_frames_sent"] = (data.get("video") or {}).get("frames_sent")
                else:
                    values[key] = {"source": (data.get("frame") or {}).get("source"),
                                   "inferences": (data.get("stats") or {}).get("inferences")}
            except (OSError, ValueError, AttributeError) as exc:
                values[key] = {"read_error": str(exc)}
        return values

    before = snapshot()
    time.sleep(1)
    after = snapshot()

    def advances(group, field):
        first, last = (sample[group].get(field) for sample in (before, after))
        return type(first) is int and type(last) is int and last > first

    owner = after["camera"]
    sequence = (bool(owner.get("epoch")) and owner["epoch"] == before["camera"].get("epoch")
                and advances("camera", "raw_seq"))
    observations = {
        "OAK RGB frames advancing": sequence,
        "Detector reporting OAK RGB and advancing": after["detector"].get("source") == "oak-rgb" and advances("detector", "inferences"),
        "Pi stereo backend reported without an error": owner.get("depth_backend") == "pi-sgbm" and not (owner.get("error") or owner.get("depth_error")),
        "Video relay counter advancing": advances("camera", "video_frames_sent"),
    }
    return {"host": host, "before": before, "after": after, "observations": observations}


def plot_walk(trajectory, output):
    import cv2
    import numpy as np
    positions = np.loadtxt(trajectory, ndmin=2)[:, 1:3]
    positions -= positions[0].copy()
    low, high = positions.min(axis=0), positions.max(axis=0)
    span = max(.2, float(np.max(high - low)))
    scale = 400 / span
    pixel = np.rint([350, 275] + (positions - (low + high) / 2) * [scale, -scale]).astype(np.int32)
    image = np.full((570, 700, 3), 250, np.uint8)
    cv2.polylines(image, [pixel], False, (135, 110, 40), 2, cv2.LINE_AA)
    cv2.circle(image, tuple(pixel[0]), 8, (40, 50, 45), 2)
    cv2.circle(image, tuple(pixel[-1]), 4, (35, 80, 210), -1)
    for value, y in (("Estimated handheld path | horizontal plane", 35),
                     (f"Ring: start   Dot: finish   |   Plot scale: {100 / scale:.2f} m per 100 pixels", 540)):
        cv2.putText(image, value, (25, y), cv2.FONT_HERSHEY_SIMPLEX, .65, (38, 45, 40), 1, cv2.LINE_AA)
    if not cv2.imwrite(str(output), image):
        raise OSError(f"Could not save {output}")


def score_walk():
    run_dir = folder("Choose the walk output folder containing dataset and run")
    values = dialog("forms", "Tape measurements for the A–M–B–M–A route", "--separator", "|",
                    "--add-entry", "A to M (metres)", "--add-entry", "M to B (metres)")
    am, mb = map(float, values.split("|"))
    if not all(math.isfinite(v) and v > 0 for v in (am, mb)):
        raise ValueError("Enter positive tape distances in metres.")
    out = Path(tempfile.mkdtemp(prefix="tape-", dir=run_dir))
    result = subprocess.run([sys.executable, BENCH / "oak_vio_score.py", "--trajectory",
                             run_dir / "run/trajectory.txt", "--gyro", run_dir / "dataset/mav0/imu0/data.csv",
                             "--route", "A", "M", "B", "M", "A", "--taped", f"A-M={am}",
                             "--taped", f"M-B={mb}", "--out", out / "score.json"], capture_output=True, text=True)
    if not (out / "score.json").exists():
        raise ValueError(result.stderr.strip() or "Missing replay files.")
    score = json.loads((out / "score.json").read_text())
    if score["scored"]:
        summary = (f"Largest tape difference: {score['worst_taped_error_m']:.3f} m.\n"
                   f"Largest separation between visits to the same mark: {score['worst_return_m']:.3f} m.")
    else:
        summary = score["reason"]
    info("Tape check", f"{summary}\n\nFull measurements and rest windows: {out / 'score.json'}")


def processing(action, release):
    names = {"Try a handheld replay": ["basalt_vio"],
             "Fit stereo cameras": ["basalt_calibrate"],
             "Fit camera and IMU": ["basalt_calibrate_imu"],
             "Replay a measured calibration and build a map": ["basalt_vio", "basalt_mapper"]}
    env = tools_environment(release, names[action])
    if subprocess.run([sys.executable, "-c", "import cv2, numpy"], capture_output=True).returncode:
        raise ValueError("Install NumPy and OpenCV in the Python environment running the launcher.")
    camera_fit = action == "Fit stereo cameras"
    imu_fit = action == "Fit camera and IMU"
    mapping = action.startswith("Replay a measured")
    config = release / "data/euroc_config.json"
    if not (camera_fit or imu_fit) and not config.is_file():
        raise ValueError(f"Missing Basalt configuration: {config}")
    prompt = {"Fit stereo cameras": "Choose the camera-grid take", "Fit camera and IMU": "Choose the moving IMU-grid take"}.get(action, "Choose a handheld walk recording")
    take = folder(prompt)
    check_take(take)
    if camera_fit:
        values = dialog("forms", "Enter measurements from your 6 x 6 AprilGrid", "--separator", "|",
                        "--add-entry", "Black tag edge (mm)", "--add-entry", "White gap (mm)")
        grid = measured_grid(*map(float, values.split("|")))
    if imu_fit or mapping:
        prior = folder("Choose the stereo-fit output folder" if imu_fit else "Choose the camera–IMU fit output folder")
        check_solution(prior / "calibration.json")
        previous = json.loads((prior / "station-step.json").read_text())
        expected = "stereo" if imu_fit else "camera-imu"
        if previous["stage"] != expected:
            raise ValueError(f"Choose the {expected} result folder from this launcher.")
        if str(take) in previous.get("recordings", []):
            raise ValueError("Select a separate take for this stage.")
        if imu_fit:
            grid = json.loads((prior / "measured-grid.json").read_text())
    destination = folder("Choose where to save this result. A fresh subfolder will be created.")
    work = Path(tempfile.mkdtemp(prefix=("camera-grid-" if camera_fit else "imu-grid-" if imu_fit else "walk-"), dir=destination))
    print(f"Writing {work}", flush=True)
    dataset = work / "dataset"
    convert = [sys.executable, BENCH / "oak_vio_convert.py", take, dataset]
    if camera_fit or imu_fit:
        convert.append("--for-calibration")
    if mapping:
        convert.extend(["--basalt-calib", prior / "calibration.json"])
    run(convert, work, env, "Preparing stereo images and IMU")
    if camera_fit or imu_fit:
        grid_path = work / "measured-grid.json"
        grid_path.write_text(json.dumps(grid, indent=2))
        cmd = [release / "bin" / names[action][0], "--dataset-type", "euroc", "--dataset-path", dataset,
               "--aprilgrid", grid_path, "--result-path", str(work) + "/"]
        solution = work / "calibration.json"
        if camera_fit:
            cmd.extend(["--cam-types", "ds", "ds"])
            steps = "Load dataset → detect corners → initialize intrinsics, poses and extrinsics → initialize optimization → optimize until convergence. Inspect corner coverage and residuals. Use save_calib, then close Basalt."
        else:
            shutil.copyfile(prior / "calibration.json", solution)
            noise_choice = choose("Camera–IMU noise values", ["Use Basalt defaults for an exploratory fit", "Enter characterized values"])
            noise = "Basalt defaults"
            if noise_choice.startswith("Enter"):
                keys = ["accel-noise-std", "gyro-noise-std", "accel-bias-std", "gyro-bias-std"]
                fields = [item for key in keys for item in ("--add-entry", key)]
                numbers = list(map(float, dialog("forms", "Enter noise densities in Basalt units", "--separator", "|", *fields).split("|")))
                if not all(math.isfinite(v) and v > 0 for v in numbers):
                    raise ValueError("All four noise values must be positive finite numbers.")
                noise = dict(zip(keys, numbers))
                for key, value in noise.items():
                    cmd.extend(["--" + key, str(value)])
            steps = "Load dataset and calibration → detect corners → initialize camera poses and camera–IMU transform → initialize optimization → optimize until convergence. Inspect the fit; enable camera time offset and IMU scale and optimize again. Use save_calib, then close Basalt."
        info("In the Basalt window", steps)
        run(cmd, work, env, "Basalt calibration")
        check_solution(solution)
        if imu_fit and json.loads(solution.read_text()) == json.loads((prior / "calibration.json").read_text()):
            raise ValueError(f"The stereo solution was unchanged. No new camera–IMU result was confirmed. See {work}.")
        (work / "station-step.json").write_text(json.dumps({"stage": "stereo" if camera_fit else "camera-imu",
            "recordings": [*previous["recordings"], str(take)] if imu_fit else [str(take)],
            "grid": grid, "noise": noise if imu_fit else None}, indent=2))
    else:
        cmd = [sys.executable, BENCH / "oak_vio_replay.py", dataset, work / "run", "--config", config,
               "--bin-dir", release / "bin"]
        if mapping:
            cmd.append("--map")
        run(cmd, work, env, "Replaying the handheld recording")
        report = json.loads((work / "run/replay.json").read_text())
        plot_walk(work / "run/trajectory.txt", work / "run/path.png")
        open_item(work / "run/path.png")
        info("Replay complete", f"{report['poses']} poses from {report['image_pairs']} image pairs.\n"
             f"Start-to-end displacement: {report['end_displacement_m']:.3f} m.\n"
             f"Duration: {report['duration_s']:.1f} s.\n\nSaved trajectory and logs in {work / 'run'}.")
        return
    info("Calibration saved", f"Use this folder for the next step:\n{work}")


def main():
    if not shutil.which("zenity"):
        sys.exit("Install zenity on the Linux desktop, then run this launcher again.")
    menu = ["Open bench camera demo", "Check bench camera signals", "Open depth-check targets",
            "Open depth-check workspace", "Try a handheld replay", "Open printable VIO AprilGrid", "Fit stereo cameras", "Fit camera and IMU",
            "Replay a measured calibration and build a map", "Score a measured walk", "Open receiving guide"]
    release = None
    while True:
        try:
            action = choose("Chennai OAK workstation", menu)
        except Cancelled:
            break
        try:
            if action == menu[0]:
                open_item(f"http://{bench_host()}:8098/demo")
            elif action == "Open depth-check workspace":
                open_item(f"http://{bench_host()}:8099/camera")
            elif action in ("Open depth-check targets", "Open printable VIO AprilGrid"):
                open_item(HANDOUTS / ("oak-targets-a4.pdf" if action == "Open depth-check targets" else "oak-aprilgrid-a4.pdf"))
            elif action == "Check bench camera signals":
                report = bench_check(bench_host())
                summary = "\n".join(("Seen: " if ok else "Check: ") + label for label, ok in report["observations"].items())
                parent = folder("Save a bench receipt in this folder")
                with tempfile.NamedTemporaryFile("w", prefix="oak-receipt-", suffix=".json", dir=parent, delete=False) as f:
                    json.dump(report, f, indent=2)
                info("Bench signals", summary + "\n\nReceipt: " + f.name)
            elif action == "Score a measured walk":
                score_walk()
            elif action == "Open receiving guide":
                open_item(ROOT / "START_HERE.html")
            else:
                if release is None:
                    release = folder("Choose the unpacked Basalt release folder (contains bin, lib and data)")
                processing(action, release)
        except Cancelled:
            continue
        except (KeyError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            release = None
            dialog("error", "This step needs attention", "--no-markup", "--text", str(exc))


if __name__ == "__main__":
    main()
