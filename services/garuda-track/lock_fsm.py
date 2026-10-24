from __future__ import annotations
from garuda_interfaces.tracked_objects import LockState


def _contains(bbox, x, y):
    bx, by, bw, bh = bbox
    return bx <= x <= bx + bw and by <= y <= by + bh


def select_by_point(tracks, x, y) -> int:
    for t in reversed(tracks):           # later tracks drawn on top -> reverse = top-most first
        if _contains(t.bbox, x, y):
            return t.track_id
    return -1


class LockFSM:
    def __init__(self, n_coast: int = 15):
        self.n_coast = n_coast; self.locked_id = -1; self.missing = 0
        self.state = LockState.SEARCHING

    def step(self, tracks, cmd):
        ids = {t.track_id for t in tracks}
        if cmd is not None:
            if cmd.cmd == "unlock":
                self.locked_id = -1; self.missing = 0; self.state = LockState.SEARCHING
            elif cmd.cmd == "lock":
                sel = select_by_point(tracks, cmd.x, cmd.y)
                if sel != -1:
                    self.locked_id = sel; self.missing = 0; self.state = LockState.LOCKED
            elif cmd.cmd == "lock_id" and cmd.track_id in ids:
                self.locked_id = cmd.track_id; self.missing = 0; self.state = LockState.LOCKED
        if self.locked_id != -1:
            if self.locked_id in ids:
                self.missing = 0; self.state = LockState.LOCKED
            else:
                self.missing += 1
                if self.missing > self.n_coast:
                    self.locked_id = -1; self.missing = 0; self.state = LockState.LOST
                else:
                    self.state = LockState.COASTING
        elif self.state != LockState.LOST:
            self.state = LockState.SEARCHING
        out = (self.locked_id, self.state)
        if self.state == LockState.LOST:     # LOST is a one-frame edge; next frame is SEARCHING
            self.state = LockState.SEARCHING
        return out
