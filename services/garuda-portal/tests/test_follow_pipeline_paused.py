"""FollowPipeline.paused — server-side STOP gesture gate (Task 4 spec §3).

Fixture shapes copied from tests/test_follow_integration.py (detset/person).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from follow_pipeline import FollowPipeline

def detset(dets):
    return {"detections": dets, "frame": {"width": 640, "height": 640}}

def person(cx, h, conf=0.9):
    return {"class_id": 0, "label": "person", "confidence": conf,
            "x": cx - 0.1, "y": 0.5 - h / 2, "w": 0.2, "h": h}

def _no_t_ns(cmd):
    return {k: v for k, v in cmd.items() if k != "t_ns"}

def test_paused_defaults_to_false_and_command_unchanged_vs_baseline():
    fp = FollowPipeline()
    fp.on_detections(detset([person(0.5, 0.5)]))          # 1st frame -> new track
    baseline = fp.on_detections(detset([person(0.5, 0.5)]))  # 2nd frame -> stable, tracking

    fp2 = FollowPipeline()
    fp2.on_detections(detset([person(0.5, 0.5)]))
    fp2.paused = False  # explicit, matches default
    unpaused = fp2.on_detections(detset([person(0.5, 0.5)]))

    assert unpaused["tracks"] == baseline["tracks"]
    assert _no_t_ns(unpaused["command"]) == _no_t_ns(baseline["command"])
    assert baseline["command"]["valid"] is True

def test_paused_true_holds_command_but_keeps_tracks():
    fp = FollowPipeline()
    fp.on_detections(detset([person(0.5, 0.5)]))
    unpaused = fp.on_detections(detset([person(0.5, 0.5)]))
    assert unpaused["command"]["valid"] is True   # sanity: was tracking before pausing

    # A second, identically-driven pipeline that pauses right before the same
    # (2nd) frame: track-list construction must be identical to the unpaused
    # run — only the command fields are gated by `paused`.
    fp2 = FollowPipeline()
    fp2.on_detections(detset([person(0.5, 0.5)]))
    fp2.paused = True
    out = fp2.on_detections(detset([person(0.5, 0.5)]))

    assert out["tracks"] == unpaused["tracks"]    # track list construction unchanged
    assert out["command"]["valid"] is False
    assert out["command"]["reason"] == "paused (gesture)"
    assert out["command"]["yaw_rate"] == 0.0
    assert out["command"]["forward_vel"] == 0.0
    assert out["command"]["vertical_vel"] == 0.0

def test_paused_false_recovers_tracking():
    fp = FollowPipeline()
    fp.on_detections(detset([person(0.5, 0.5)]))
    fp.on_detections(detset([person(0.5, 0.5)]))

    fp.paused = True
    held = fp.on_detections(detset([person(0.5, 0.5)]))
    assert held["command"]["valid"] is False

    fp.paused = False
    out = fp.on_detections(detset([person(0.5, 0.5)]))
    assert out["command"]["valid"] is True

def test_set_pinned_clears_paused():
    """spec §3: a video click (which pins) clears a gesture STOP."""
    fp = FollowPipeline()
    fp.on_detections(detset([person(0.5, 0.5)]))
    out = fp.on_detections(detset([person(0.5, 0.5)]))
    tid = out["tracks"][0]["id"]

    fp.paused = True
    assert fp.paused is True
    fp.set_pinned(tid)
    assert fp.paused is False
