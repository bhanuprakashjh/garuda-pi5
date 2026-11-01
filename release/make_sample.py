#!/usr/bin/env python3
"""Generate our own BSD-3-Clause playback fixture; no external footage or accuracy claim."""
import argparse
from pathlib import Path
import cv2
import numpy as np


def generate(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.stem + '.partial.avi')
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*'MJPG'), 20, (640, 360))
    if not writer.isOpened():
        raise RuntimeError('OpenCV MJPEG encoder unavailable')
    try:
        for i in range(120):
            frame = np.full((360, 640, 3), (30, 25, 20), dtype=np.uint8)
            cv2.putText(frame, 'GARUDA REPLAY CHECK', (22, 42), cv2.FONT_HERSHEY_SIMPLEX, .85, (230, 230, 230), 2)
            cv2.putText(frame, 'Synthetic shapes - not a detection benchmark', (22, 75), cv2.FONT_HERSHEY_SIMPLEX, .48, (180, 200, 220), 1)
            for x in range(0, 640, 40):
                cv2.line(frame, (x, 100), (x, 320), (45, 45, 45), 1)
            x = 45 + i * 4
            cv2.circle(frame, (x, 155), 19, (60, 180, 250), -1)
            cv2.line(frame, (x, 175), (x, 245), (60, 180, 250), 12)
            cv2.line(frame, (x-30, 220), (x+30, 190), (60, 180, 250), 7)
            cv2.line(frame, (x, 245), (x-22, 290), (60, 180, 250), 7)
            cv2.line(frame, (x, 245), (x+22, 290), (60, 180, 250), 7)
            cv2.putText(frame, f'Frame {i+1:03d}/120  |  20 fps', (22, 345), cv2.FONT_HERSHEY_SIMPLEX, .55, (220, 220, 220), 1)
            writer.write(frame)
    finally:
        writer.release()
    cap = cv2.VideoCapture(str(tmp))
    okay = cap.isOpened() and int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 120
    cap.release()
    if not okay:
        tmp.unlink(missing_ok=True)
        raise RuntimeError('Generated video failed decode/frame-count check')
    tmp.replace(output)
    print(output)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'samples/playback-check.avi')
    generate(p.parse_args().output)
