import sys, pathlib, queue, threading, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from follow_pipeline import FollowPipeline
from events import EventBus
from portal import follow_publisher_loop

def detset(dets):
    return {"detections": dets, "frame": {"width": 640, "height": 640}}

def person(cx, h, conf=0.9):
    return {"class_id": 0, "label": "person", "confidence": conf,
            "x": cx - 0.1, "y": 0.5 - h / 2, "w": 0.2, "h": h}

def test_pipeline_tracks_and_commands():
    fp = FollowPipeline()
    fp.on_detections(detset([person(0.5, 0.5)]))
    out = fp.on_detections(detset([person(0.5, 0.5)]))   # 2nd frame -> stable track
    assert len(out["tracks"]) == 1
    assert out["tracks"][0]["id"] == 1
    assert out["command"]["valid"] is True

def test_pipeline_holds_when_no_person():
    fp = FollowPipeline()
    out = fp.on_detections(detset([{"class_id": 2, "label": "car", "confidence": 0.9,
                                    "x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}]))
    assert out["command"]["valid"] is False

def test_pinned_id_is_followed():
    fp = FollowPipeline()
    fp.on_detections(detset([person(0.3, 0.5), person(0.7, 0.5)]))
    out = fp.on_detections(detset([person(0.3, 0.5), person(0.7, 0.5)]))
    fp.set_pinned(out["tracks"][1]["id"])
    out2 = fp.on_detections(detset([person(0.3, 0.5), person(0.7, 0.5)]))
    assert out2["command"]["target_id"] == out["tracks"][1]["id"]

# --- Follow-me re-acquire: a pinned subject that leaves and returns re-locks by
# class (no re-ID -> new id). Portal-only tuning; select() itself stays strict. ---

def _drop_track(fp, frames=50):
    """Feed empty frames until the pinned track ages out (max_age=45)."""
    for _ in range(frames):
        fp.on_detections(detset([]))

def test_reacquire_relocks_same_class_after_pinned_subject_returns():
    fp = FollowPipeline()  # reacquire on by default
    fp.on_detections(detset([person(0.5, 0.5)]))
    out = fp.on_detections(detset([person(0.5, 0.5)]))
    pid = out["tracks"][0]["id"]
    fp.set_pinned(pid)
    fp.on_detections(detset([person(0.5, 0.5)]))      # one visible frame -> records pinned class
    _drop_track(fp)                                   # subject leaves -> track aged out
    fp.on_detections(detset([person(0.5, 0.5)]))      # returns with a NEW id
    out2 = fp.on_detections(detset([person(0.5, 0.5)]))
    assert out2["command"]["valid"] is True
    assert out2["command"]["target_id"] == out2["tracks"][0]["id"]  # re-pinned to the returner
    assert out2["tracks"][0]["id"] != pid                          # new id (no re-ID)

def test_reacquire_off_holds_after_pinned_subject_lost():
    fp = FollowPipeline(reacquire=False)              # strict hold (select()'s W1 contract)
    fp.on_detections(detset([person(0.5, 0.5)]))
    out = fp.on_detections(detset([person(0.5, 0.5)]))
    fp.set_pinned(out["tracks"][0]["id"])
    _drop_track(fp)
    fp.on_detections(detset([person(0.5, 0.5)]))
    out2 = fp.on_detections(detset([person(0.5, 0.5)]))
    assert out2["command"]["valid"] is False          # never re-acquires, never auto-switches

def test_reacquire_grace_holds_through_brief_occlusion_not_bystander():
    """A pinned subject briefly occluded while a same-class bystander is visible
    must HOLD (not snap to the bystander) during the grace period, then resume on
    its ORIGINAL id when it reappears (tracker re-association within max_age)."""
    fp = FollowPipeline()  # reacquire on, grace = 15 frames
    fp.on_detections(detset([person(0.3, 0.5), person(0.7, 0.5)]))
    out = fp.on_detections(detset([person(0.3, 0.5), person(0.7, 0.5)]))
    a = out["tracks"][0]["id"]                       # the left subject we pin
    fp.set_pinned(a)
    fp.on_detections(detset([person(0.3, 0.5), person(0.7, 0.5)]))   # sees A -> records class
    for _ in range(5):                               # A occluded (< grace); only bystander visible
        out = fp.on_detections(detset([person(0.7, 0.5)]))
        assert out["command"]["valid"] is False      # HOLD — did NOT switch to the bystander
    out = fp.on_detections(detset([person(0.3, 0.5), person(0.7, 0.5)]))  # A returns to its spot
    assert out["command"]["target_id"] == a          # resumes on its original id

def test_reacquire_will_not_lock_a_different_class():
    fp = FollowPipeline()
    fp.on_detections(detset([person(0.5, 0.5)]))
    out = fp.on_detections(detset([person(0.5, 0.5)]))
    fp.set_pinned(out["tracks"][0]["id"])
    fp.on_detections(detset([person(0.5, 0.5)]))      # records pinned class = person
    _drop_track(fp)
    car = {"class_id": 2, "label": "car", "confidence": 0.9, "x": 0.4, "y": 0.4, "w": 0.2, "h": 0.3}
    fp.on_detections(detset([car]))
    out2 = fp.on_detections(detset([car]))
    assert out2["command"]["valid"] is False          # pinned a person; a car must not be re-acquired

# --- Resilience (fix-report addendum): a malformed detection must not raise
# out of FollowPipeline, and a raising pipeline must not kill the portal's
# follow_publisher_loop thread. See the "Fix" section of task-7-report.md. ---

def test_on_detections_skips_detection_missing_required_key():
    """A detection dict missing a key Tracker/iou index directly (here: "h")
    must not raise — FollowPipeline treats it as if absent this frame rather
    than propagating a KeyError."""
    fp = FollowPipeline()
    missing_h = {"class_id": 0, "label": "person", "confidence": 0.9,
                 "x": 0.4, "y": 0.25, "w": 0.2}  # no "h"
    out = fp.on_detections(detset([missing_h]))
    assert out["tracks"] == []
    assert out["command"]["valid"] is False

def test_on_detections_keeps_working_on_mixed_good_and_bad_detections():
    """One malformed detection alongside a well-formed one: the good one is
    still tracked normally; the bad one is silently dropped, not raised."""
    fp = FollowPipeline()
    missing_class_id = {"label": "person", "confidence": 0.9,
                        "x": 0.4, "y": 0.25, "w": 0.2, "h": 0.5}  # no "class_id"
    fp.on_detections(detset([person(0.5, 0.5), missing_class_id]))
    out = fp.on_detections(detset([person(0.5, 0.5), missing_class_id]))
    assert len(out["tracks"]) == 1
    assert out["command"]["valid"] is True

class _RaisingOnceThenOkPipeline:
    """Test double standing in for FollowPipeline: the first on_detections
    call raises (simulating some unexpected failure deep in the real
    pipeline — e.g. a non-dict detections payload from a corrupted/rewritten
    schema), subsequent calls succeed. Proves follow_publisher_loop's
    per-event try/except keeps the subscriber thread alive and processing
    after a bad frame, matching InferenceManager's own resilience pattern."""
    def __init__(self):
        self.calls = 0

    def on_detections(self, detset_dict):
        self.calls += 1
        if self.calls == 1:
            raise KeyError("h")
        return {"tracks": [], "command": {"valid": False, "reason": "no target"}}

def test_follow_publisher_loop_survives_a_raising_pipeline_and_keeps_running():
    bus = EventBus()
    pipeline = _RaisingOnceThenOkPipeline()
    listener = bus.subscribe()

    t = threading.Thread(target=follow_publisher_loop, args=(bus, pipeline), daemon=True)
    t.start()
    time.sleep(0.05)  # let the loop subscribe before we publish

    bus.publish("detections", detset([person(0.5, 0.5)]))  # 1st call -> raises internally
    bus.publish("detections", detset([person(0.5, 0.5)]))  # 2nd call -> must still be processed

    # The listener also sees the two raw "detections" events it published
    # itself (bus fan-out) alongside the loop's republished "error"/"follow" —
    # keep draining until both of the loop's outputs have shown up.
    kinds = []
    deadline = time.time() + 2.0
    while time.time() < deadline and not {"error", "follow"} <= set(kinds):
        try:
            ev = listener.get(timeout=0.5)
        except queue.Empty:
            continue
        kinds.append(ev["kind"])

    assert "error" in kinds        # the first (raising) event was signaled, not swallowed silently
    assert "follow" in kinds       # the second event still made it through -> thread didn't die
    assert pipeline.calls == 2     # both events were actually handed to the pipeline
