import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))  # garuda_interfaces
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))                  # track_ipc (sibling)

import pytest
from garuda_interfaces.tracked_objects import TrackedObjects, TrackedObject, LockState, ObjState
from track_ipc import TrackIPC


def _msg(seq):
    return TrackedObjects(t_capture_ns=seq, frame_w=640, frame_h=480,
        objects=[TrackedObject(1, 0, "person", 0.9, [1, 2, 3, 4], 1, ObjState.CONFIRMED)],
        locked_id=1, lock_state=LockState.LOCKED, seq=seq)


def test_write_read_roundtrip(tmp_path):
    p = str(tmp_path / "t.ipc")
    w = TrackIPC(p, create=True); r = TrackIPC(p, create=False)
    assert r.read_latest() is None            # nothing written yet
    w.write(_msg(5))
    got = r.read_latest()
    assert got.seq == 5 and got.objects[0].track_id == 1
    w.write(_msg(6))
    assert r.read_latest().seq == 6           # latest-wins
    w.close(); r.close()


def test_torn_payload_returns_none(tmp_path):
    import struct
    w = TrackIPC(str(tmp_path / "x.ipc"), create=True)
    garbage = b"{not valid json"
    w.mm[12:12 + len(garbage)] = garbage
    struct.pack_into("<QI", w.mm, 0, 9, len(garbage))    # seq=9, len -> looks fresh, payload is junk
    assert w.read_latest() is None                       # tolerated, never crashes the reader
    w.close()


def test_oversize_raises(tmp_path):
    w = TrackIPC(str(tmp_path / "s.ipc"), create=True, max_bytes=64)
    big = TrackedObjects(0, 640, 480, [TrackedObject(i, 0, "x", 0.5, [0, 0, 1, 1]) for i in range(50)], -1, LockState.SEARCHING, 1)
    with pytest.raises(ValueError):
        w.write(big)
    w.close()


def test_seq_zero_rejected(tmp_path):
    w = TrackIPC(str(tmp_path / "z.ipc"), create=True)
    zero_seq = TrackedObjects(t_capture_ns=1, frame_w=640, frame_h=480,
        objects=[TrackedObject(1, 0, "person", 0.9, [1, 2, 3, 4], 1, ObjState.CONFIRMED)],
        locked_id=1, lock_state=LockState.LOCKED, seq=0)
    with pytest.raises(ValueError, match="seq must be >= 1"):
        w.write(zero_seq)
    w.close()
