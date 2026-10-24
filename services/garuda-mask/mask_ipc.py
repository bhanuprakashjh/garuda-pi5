#!/usr/bin/env python3
"""MaskIPC — a single-slot, latest-wins shared mask over an mmap'd file.

Why a file (not /dev/shm SharedMemory): the consumer in the ORB-SLAM3 path (Task 3) runs in
a container, and a bind-mounted file is the clean way to hand a mask across that boundary.
One writer (the masker), one-or-many readers. No queue: a reader always gets the newest mask
or nothing — tracking must never block on a stale frame (RDS-SLAM's "use latest available").

Layout:  [seq u64][t_ns u64][h u32][w u32][ h*w uint8 payload ]
The writer stamps the payload first and the header (with seq) last; the reader reads seq,
copies the payload, re-reads seq, and retries on a mismatch — so a torn read is never returned.
"""
from __future__ import annotations

import mmap
import struct

import numpy as np

_HDR = struct.Struct("<QQII")   # seq, t_ns, h, w  -> 24 bytes


class MaskIPC:
    def __init__(self, path: str, shape, create: bool):
        self.path = str(path)
        self.h, self.w = int(shape[0]), int(shape[1])
        self.size = _HDR.size + self.h * self.w
        if create:
            with open(self.path, "wb") as f:
                f.write(b"\x00" * self.size)     # zeroed => seq 0 => "nothing written yet"
        self.f = open(self.path, "r+b")
        self.mm = mmap.mmap(self.f.fileno(), self.size)

    def write(self, seq: int, t_ns: int, mask) -> None:
        m = np.ascontiguousarray(mask, dtype=np.uint8)
        if m.shape != (self.h, self.w):
            raise ValueError(f"mask shape {m.shape} != IPC shape {(self.h, self.w)}")
        self.mm[_HDR.size:self.size] = m.tobytes()               # payload first
        _HDR.pack_into(self.mm, 0, int(seq), int(t_ns), self.h, self.w)   # seq last (release)

    def read_latest(self):
        """Returns (seq, t_ns, mask HxW uint8) or None if nothing has been written."""
        seq1 = t_ns = h = w = 0
        payload = b""
        for _ in range(8):
            seq1, t_ns, h, w = _HDR.unpack_from(self.mm, 0)
            if seq1 == 0:
                return None
            payload = bytes(self.mm[_HDR.size:_HDR.size + h * w])
            seq2, _, _, _ = _HDR.unpack_from(self.mm, 0)
            if seq1 == seq2:                                      # consistent snapshot
                break
        return seq1, t_ns, np.frombuffer(payload, np.uint8).reshape(h, w)

    def close(self) -> None:
        try:
            self.mm.close()
        except Exception:
            pass
        try:
            self.f.close()
        except Exception:
            pass
