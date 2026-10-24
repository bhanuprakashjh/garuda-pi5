from __future__ import annotations
import mmap, struct
from garuda_interfaces.tracked_objects import TrackedObjects

_HDR = struct.Struct("<QI")   # seq, payload_len


class TrackIPC:
    def __init__(self, path: str, create: bool, max_bytes: int = 65536):
        self.path = str(path); self.size = _HDR.size + int(max_bytes); self.cap = int(max_bytes)
        if create:
            with open(self.path, "wb") as f:
                f.write(b"\x00" * self.size)
        self.f = open(self.path, "r+b")
        self.mm = mmap.mmap(self.f.fileno(), self.size)

    def write(self, msg: TrackedObjects) -> None:
        if int(msg.seq) < 1:
            raise ValueError(f"seq must be >= 1 (0 is the empty-file sentinel), got {msg.seq}")
        payload = msg.to_json()
        if len(payload) > self.cap:
            raise ValueError(f"payload {len(payload)} > cap {self.cap}")
        self.mm[_HDR.size:_HDR.size + len(payload)] = payload         # payload first
        _HDR.pack_into(self.mm, 0, int(msg.seq), len(payload))        # seq last (release)

    def read_latest(self):
        for _ in range(8):
            seq1, n = _HDR.unpack_from(self.mm, 0)
            if seq1 == 0:
                return None
            payload = bytes(self.mm[_HDR.size:_HDR.size + n])
            seq2, _ = _HDR.unpack_from(self.mm, 0)
            if seq1 == seq2:
                try:
                    return TrackedObjects.from_json(payload)
                except (ValueError, KeyError):   # a torn payload slipped the seqlock -> retry, never crash the reader
                    continue
        return None

    def close(self):
        try:
            self.mm.close()
        finally:
            self.f.close()
