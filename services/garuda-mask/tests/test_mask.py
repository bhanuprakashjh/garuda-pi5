#!/usr/bin/env python3
"""Unit tests for the dynamic-masking service — hardware-free (numpy + cv2 only, no Hailo).

Runs under pytest (repo convention) OR standalone on the bench where pytest isn't
installed:  `python3 services/garuda-mask/tests/test_mask.py`  (exits non-zero on failure).
The Hailo YOLOv5-seg decode is exercised by the harness (hailo_yolo_seg.py --probe),
not here — this file pins the pure logic: dynamic-class union/resize/dilate + latest-wins IPC.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mask_ipc import MaskIPC
from mask_service import build_dynamic_mask


def test_dynamic_union_resize_dilate():
    person = np.zeros((160, 160), np.uint8); person[60:80, 60:80] = 1
    chair = np.zeros((160, 160), np.uint8); chair[100:120, 100:120] = 1
    out = build_dynamic_mask([("person", person), ("chair", chair)],
                             dynamic={"person"}, out_hw=(160, 160), dilate_px=3)
    assert out.shape == (160, 160) and out.dtype == np.uint8
    assert out[70, 70] == 1                 # person region masked
    assert out[110, 110] == 0               # chair is static -> NOT masked
    assert out[57, 70] == 1                 # dilated ~3px above person top (row 60 -> reaches 57)
    assert out.sum() > 20 * 20              # dilation enlarged the raw 20x20 blob


def test_resizes_to_out_hw():
    person = np.zeros((160, 160), np.uint8); person[:, 0:80] = 1   # left half at 160-res
    out = build_dynamic_mask([("person", person)], dynamic={"person"},
                             out_hw=(480, 640), dilate_px=0)
    assert out.shape == (480, 640)
    assert out[240, 10] == 1 and out[240, 600] == 0                # left half masked after resize


def test_empty_when_no_dynamic_instances():
    chair = np.ones((160, 160), np.uint8)
    out = build_dynamic_mask([("chair", chair)], dynamic={"person"},
                             out_hw=(100, 120), dilate_px=5)
    assert out.shape == (100, 120) and out.sum() == 0


def test_ipc_latest_wins():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "mask.shm")
    w = MaskIPC(path, shape=(8, 8), create=True)
    r = MaskIPC(path, shape=(8, 8), create=False)
    try:
        assert r.read_latest() is None                  # nothing written yet
        a = np.ones((8, 8), np.uint8); b = np.zeros((8, 8), np.uint8)
        w.write(seq=1, t_ns=100, mask=a)
        w.write(seq=2, t_ns=200, mask=b)                 # overwrite before any read
        seq, t_ns, mask = r.read_latest()
        assert seq == 2 and t_ns == 200                  # reader sees ONLY the latest, no queue
        assert (mask == b).all()
    finally:
        w.close(); r.close()


def test_ipc_roundtrip_payload():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "mask.shm")
    w = MaskIPC(path, shape=(30, 40), create=True)
    r = MaskIPC(path, shape=(30, 40), create=False)
    try:
        m = (np.arange(30 * 40).reshape(30, 40) % 2).astype(np.uint8)
        w.write(seq=7, t_ns=12345, mask=m)
        seq, t_ns, got = r.read_latest()
        assert seq == 7 and t_ns == 12345
        assert got.shape == (30, 40) and (got == m).all()
    finally:
        w.close(); r.close()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:                      # noqa: BLE001 - test runner
            fails += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
