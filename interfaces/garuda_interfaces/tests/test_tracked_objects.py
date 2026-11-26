import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # -> interfaces/ so `garuda_interfaces` imports

from garuda_interfaces.tracked_objects import (
    TrackedObject, TrackedObjects, LockCommand, LockState, ObjState)


def test_tracked_objects_json_roundtrip():
    o = TrackedObject(track_id=7, cls=0, cls_name="person", conf=0.91,
                      bbox=[10.0, 20.0, 30.0, 40.0], age=5, state=ObjState.CONFIRMED)
    assert o.center == [25.0, 40.0]
    msg = TrackedObjects(t_capture_ns=123, frame_w=640, frame_h=480, objects=[o],
                         locked_id=7, lock_state=LockState.LOCKED, seq=2)
    back = TrackedObjects.from_json(msg.to_json())
    assert back == msg
    assert back.objects[0].cls_name == "person"


def test_empty_and_lock_command():
    m = TrackedObjects(t_capture_ns=0, frame_w=640, frame_h=480, objects=[],
                       locked_id=-1, lock_state=LockState.SEARCHING, seq=1)
    assert TrackedObjects.from_json(m.to_json()).objects == []
    c = LockCommand.from_json(LockCommand(cmd="lock", x=100, y=50).to_json())
    assert c.cmd == "lock" and c.x == 100 and c.y == 50
