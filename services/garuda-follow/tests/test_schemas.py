import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
from garuda_interfaces import Track, FollowCommand, SCHEMA_VERSION

def test_track_to_dict_roundtrips_fields():
    t = Track(id=3, class_id=0, label="person", bbox=(0.1, 0.2, 0.3, 0.4),
              confidence=0.82, hits=5, age=5, time_since_update=0)
    d = t.to_dict()
    assert d["id"] == 3 and d["label"] == "person"
    assert d["bbox"] == [0.1, 0.2, 0.3, 0.4]        # tuple serialized as list
    assert d["confidence"] == 0.82 and d["time_since_update"] == 0
    assert d["schema"] == SCHEMA_VERSION            # crosses the SSE boundary to the UI

def test_follow_command_hold_shape():
    c = FollowCommand(t_ns=123, target_id=None, yaw_rate=0.0, forward_vel=0.0,
                      vertical_vel=0.0, valid=False, reason="no target")
    d = c.to_dict()
    assert d["valid"] is False and d["reason"] == "no target"
    assert d["yaw_rate"] == 0.0 and d["target_id"] is None
