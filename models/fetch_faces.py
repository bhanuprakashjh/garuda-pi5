#!/usr/bin/env python3
"""Fetch checksum-pinned OpenCV face models; no package/driver installation."""
import argparse
import hashlib
from pathlib import Path
import urllib.request

REV = "48b10fa505aeb51fbd127fc60a5c660040e823b7"
FILES = {
    "yunet.onnx": ("face_detection_yunet/face_detection_yunet_2023mar.onnx", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "sface.onnx": ("face_recognition_sface/face_recognition_sface_2021dec.onnx", "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
    "yunet.LICENSE": ("face_detection_yunet/LICENSE", "c83b8120c50ccbd4c4f96edf53141bdd566ebb8f8e9227e415326aa1b1aba958"),
    "sface.LICENSE": ("face_recognition_sface/LICENSE", "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "faces")
    args = parser.parse_args(); args.out.mkdir(parents=True, exist_ok=True)
    for name, (path, sha) in FILES.items():
        dest = args.out/name
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == sha:
            print(name, "verified"); continue
        base = "https://media.githubusercontent.com/media" if name.endswith(".onnx") else "https://raw.githubusercontent.com"
        url = f"{base}/opencv/opencv_zoo/{REV}/models/{path}"
        with urllib.request.urlopen(url, timeout=90) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != sha:
            raise RuntimeError(f"Checksum mismatch: {name}")
        temporary=dest.with_suffix(dest.suffix+'.tmp');temporary.write_bytes(data);temporary.replace(dest)
        print(name, "downloaded and verified")


if __name__ == "__main__":
    main()
