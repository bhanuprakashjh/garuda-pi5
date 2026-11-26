from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from enum import Enum


class LockState(str, Enum):
    SEARCHING = "SEARCHING"; LOCKED = "LOCKED"; COASTING = "COASTING"; LOST = "LOST"


class ObjState(str, Enum):
    TENTATIVE = "tentative"; CONFIRMED = "confirmed"; COASTING = "coasting"


@dataclass
class TrackedObject:
    track_id: int
    cls: int
    cls_name: str
    conf: float
    bbox: list          # [x, y, w, h] px, top-left origin
    age: int = 0
    state: ObjState = ObjState.TENTATIVE

    @property
    def center(self) -> list:
        return [self.bbox[0] + self.bbox[2] / 2.0, self.bbox[1] + self.bbox[3] / 2.0]


@dataclass
class TrackedObjects:
    t_capture_ns: int
    frame_w: int
    frame_h: int
    objects: list = field(default_factory=list)
    locked_id: int = -1
    lock_state: LockState = LockState.SEARCHING
    seq: int = 0

    def to_json(self) -> bytes:
        d = asdict(self)
        d["lock_state"] = self.lock_state.value
        for od in d["objects"]:
            od["state"] = od["state"].value if isinstance(od["state"], ObjState) else od["state"]
        return json.dumps(d, separators=(",", ":")).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> "TrackedObjects":
        d = json.loads(raw)
        objs = [TrackedObject(**{**o, "state": ObjState(o["state"])}) for o in d["objects"]]
        d["objects"] = objs
        d["lock_state"] = LockState(d["lock_state"])
        return cls(**d)


@dataclass
class LockCommand:
    cmd: str            # "lock" | "lock_id" | "unlock"
    x: int = -1
    y: int = -1
    track_id: int = -1

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":")).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> "LockCommand":
        return cls(**json.loads(raw))
