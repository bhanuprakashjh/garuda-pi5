import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))  # garuda_interfaces
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))                  # service modules

from track_service import TrackService, coco_name          # noqa: E402
from track_ipc import TrackIPC                              # noqa: E402
from bytetrack import Track                                 # noqa: E402
from garuda_interfaces.tracked_objects import LockCommand, LockState  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_ids():
    Track._next = 1


def test_coco_name():
    assert coco_name(0) == "person"
    assert coco_name(2) == "car"
    assert coco_name(999).startswith("cls")


def test_process_lock_persists(tmp_path):
    svc = TrackService(str(tmp_path / "t.ipc"), str(tmp_path / "c.sock"),
                       min_hits=1, open_cmd=False)
    frames = [[([x, 40, 20, 20], 0, 0.9)] for x in range(0, 60, 10)]  # person drifting right
    m0 = svc.process(0, 640, 480, frames[0])                          # establish track (id 1)
    assert m0.objects[0].track_id == 1 and m0.objects[0].cls_name == "person"
    assert m0.lock_state == LockState.SEARCHING
    # click on the object to lock it
    m1 = svc.process(1, 640, 480, frames[1], cmds=[LockCommand("lock", x=15, y=50)])
    assert m1.locked_id == 1 and m1.lock_state == LockState.LOCKED
    later = [svc.process(i + 2, 640, 480, f) for i, f in enumerate(frames[2:])]
    assert all(m.locked_id == 1 and m.lock_state == LockState.LOCKED for m in later)
    svc.close()


def test_labels_override_coco(tmp_path):
    svc = TrackService(str(tmp_path / "t.ipc"), str(tmp_path / "c.sock"),
                       min_hits=1, open_cmd=False)
    # detector-supplied label wins over the built-in COCO table for the same class id
    m = svc.process(0, 640, 480, [([0, 0, 20, 20], 0, 0.9)], labels={0: "operator"})
    assert m.objects[0].cls_name == "operator"
    # absent label falls back to the COCO name
    m2 = svc.process(1, 640, 480, [([0, 0, 20, 20], 0, 0.9)], labels={})
    assert m2.objects[0].cls_name == "person"
    svc.close()


def test_lock_then_coasting_through_process(tmp_path):
    svc = TrackService(str(tmp_path / "t.ipc"), str(tmp_path / "c.sock"),
                       min_hits=1, n_coast=2, open_cmd=False)
    det = [([0, 0, 20, 20], 0, 0.9)]
    svc.process(0, 640, 480, det)
    m = svc.process(1, 640, 480, det, cmds=[LockCommand("lock", x=5, y=5)])
    assert m.lock_state == LockState.LOCKED
    m2 = svc.process(2, 640, 480, [])                     # target gone -> coasting
    assert m2.locked_id == 1 and m2.lock_state == LockState.COASTING
    svc.close()


def test_ipc_published(tmp_path):
    svc = TrackService(str(tmp_path / "t.ipc"), str(tmp_path / "c.sock"),
                       min_hits=1, open_cmd=False)
    svc.process(5, 640, 480, [([0, 0, 20, 20], 2, 0.8)])
    reader = TrackIPC(str(tmp_path / "t.ipc"), create=False)
    got = reader.read_latest()
    assert got is not None and got.seq == 1
    assert got.objects[0].cls_name == "car" and got.t_capture_ns == 5
    reader.close()
    svc.close()
