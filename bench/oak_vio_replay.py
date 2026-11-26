#!/usr/bin/env python3
"""Replay a converted handheld OAK dataset with Basalt and retain its mapping inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import numpy as np


def replay(dataset, out, config, bin_dir=None, map_scene=False):
    dataset, out = Path(dataset).resolve(), Path(out).resolve()
    calibration = dataset / "basalt_calib.json"
    if not calibration.is_file():
        raise ValueError("need a replay dataset with basalt_calib.json; calibration captures have raw images only")
    conversion = json.loads((dataset / "conversion.json").read_text())
    names = ["basalt_vio", "basalt_mapper"] if map_scene else ["basalt_vio"]
    executables = {}
    for name in names:
        command = str(Path(bin_dir).resolve() / name) if bin_dir else name
        executables[name] = shutil.which(command)
        if executables[name] is None:
            raise FileNotFoundError(f"install {name} or specify --bin-dir")
    config_bytes = Path(config).read_bytes()
    out.mkdir(parents=True, exist_ok=False)
    (out / "config.json").write_bytes(config_bytes)
    shutil.copyfile(calibration, out / "basalt_calib.json")
    shared = ["--cam-calib", "basalt_calib.json", "--config-path", "config.json",
              "--show-gui", "0", "--marg-data", "marg"]

    def run(name, args):
        with (out / (name + ".log")).open("w") as log:
            subprocess.run([executables[name], *shared, *args], cwd=out, stdout=log,
                           stderr=subprocess.STDOUT, check=True)

    run("basalt_vio", ["--dataset-path", str(dataset), "--dataset-type", "euroc",
                       "--save-trajectory", "tum"])
    poses = np.loadtxt(out / "trajectory.txt", ndmin=2)
    if (poses.shape[0] < 2 or poses.shape[1] != 8 or not np.isfinite(poses).all()
            or not np.all(np.diff(poses[:, 0]) > 0)):
        raise ValueError("Basalt returned an unusable trajectory; see basalt_vio.log")
    marg_files = sum(p.stem.isdigit() for p in (out / "marg").glob("*.cereal"))
    tracks = None
    if map_scene:
        if not marg_files:
            raise ValueError("Basalt produced no keyframe inputs for the mapper")
        run("basalt_mapper", [])
        with (out / "basalt_mapper.log").open() as log:
            for line in log:
                match = re.search(r"Built (\d+) feature tracks", line)
                if match:
                    tracks = int(match.group(1))
        if tracks == 0:
            raise ValueError("the mapper ran but built zero feature tracks; see basalt_mapper.log")
    report = {
        "image_pairs": conversion["pairs"], "poses": len(poses),
        "duration_s": float(poses[-1, 0] - poses[0, 0]),
        "end_displacement_m": float(np.linalg.norm(poses[-1, 1:4] - poses[0, 1:4])),
        "marginalization_files": marg_files, "mapper_completed": map_scene,
        "mapper_feature_tracks": tracks,
        "effective_calibration_sha256": hashlib.sha256((out / "basalt_calib.json").read_bytes()).hexdigest(),
        "source_calibration_sha256": conversion.get("source_calibration_sha256"),
        "camera_timestamp_shift_ns": conversion.get("camera_timestamp_shift_ns", 0),
    }
    (out / "replay.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("out", type=Path, help="new run directory")
    parser.add_argument("--config", type=Path, required=True, help="Basalt VIO config, e.g. euroc_config.json")
    parser.add_argument("--bin-dir", type=Path, help="directory containing the Basalt executables; default uses PATH")
    parser.add_argument("--map", action="store_true", help="also run the offline Basalt mapper")
    args = parser.parse_args()
    print(json.dumps(replay(args.dataset, args.out, args.config, args.bin_dir, args.map), indent=2))
