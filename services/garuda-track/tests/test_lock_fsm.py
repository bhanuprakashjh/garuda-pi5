import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))  # garuda_interfaces
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))                  # lock_fsm (sibling)

from lock_fsm import LockFSM, select_by_point
from garuda_interfaces.tracked_objects import LockCommand, LockState


class T:
    def __init__(self, tid, bbox): self.track_id = tid; self.bbox = bbox


def test_select_by_point_topmost():
    ts = [T(1, [0, 0, 100, 100]), T(2, [40, 40, 20, 20])]   # 2 is on top (later)
    assert select_by_point(ts, 50, 50) == 2
    assert select_by_point(ts, 5, 5) == 1
    assert select_by_point(ts, 500, 500) == -1


def test_lock_coast_lost_cycle():
    fsm = LockFSM(n_coast=2)
    ts = [T(1, [0, 0, 10, 10])]
    assert fsm.step(ts, None) == (-1, LockState.SEARCHING)
    assert fsm.step(ts, LockCommand("lock", x=5, y=5)) == (1, LockState.LOCKED)
    assert fsm.step([], None) == (1, LockState.COASTING)       # missing 1 frame
    assert fsm.step([], None) == (1, LockState.COASTING)       # missing 2 (== n_coast)
    assert fsm.step([], None) == (-1, LockState.LOST)          # exceeded -> drop
    assert fsm.step(ts, None) == (-1, LockState.SEARCHING)     # next frame searching


def test_unlock_and_reacquire():
    fsm = LockFSM(n_coast=5)
    ts = [T(1, [0, 0, 10, 10])]
    fsm.step(ts, LockCommand("lock", x=5, y=5))
    assert fsm.step(ts, LockCommand("unlock")) == (-1, LockState.SEARCHING)
    fsm.step(ts, LockCommand("lock_id", track_id=1))
    assert fsm.step([], None) == (1, LockState.COASTING)       # id gone -> coast, not lost
    assert fsm.step(ts, None) == (1, LockState.LOCKED)         # reappears -> re-locked
