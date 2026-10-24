"""GestureReactor — server-side gesture semantics for the Tracking tab (Task 4).

Fixture shapes: `mono` bus events carrying a v2 snapshot (hosts/pi5/mono_state.py
build_snapshot), tracks[].x/y/w/h in SOURCE-FRAME PIXELS, top-left origin —
same convention exercised in tests/test_mono_client.py's SNAP fixture.
"""
import sys, pathlib, queue, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from events import EventBus
from gesture_actions import GestureReactor

class StubPipeline:
    """Records attribute sets/calls instead of doing real tracking."""
    def __init__(self):
        self.paused = False
        self.pinned_id = "unset"
        self.set_pinned_calls = []

    def set_pinned(self, track_id):
        self.pinned_id = track_id
        self.set_pinned_calls.append(track_id)

def snap(gesture=None, tracks=None):
    return {"v": 2, "tracks": tracks or [], "frame": {"width": 1280, "height": 720},
            "gesture": gesture}

def _start(bus, pipeline, mono_base="http://mono.example:8098"):
    calls = []
    reactor = GestureReactor(bus, pipeline, mono_base, post=lambda url: calls.append(url))
    reactor.start()
    return reactor, calls

def _publish_and_settle(bus, events, settle_s=0.15):
    """Publish each event and give the reactor's daemon thread time to drain
    the queue (it subscribes asynchronously in .start())."""
    time.sleep(0.05)  # let the reactor subscribe before the first publish
    for ev in events:
        bus.publish("mono", ev)
    time.sleep(settle_s)

# --- (a) stop onset: exactly one action despite repeats ---------------------

def test_stop_onset_pauses_pipeline_once_despite_repeats():
    bus = EventBus()
    pipeline = StubPipeline()
    reactor, calls = _start(bus, pipeline)

    events = [snap(gesture={"name": None, "track_id": None, "held_ms": 0})]
    events += [snap(gesture={"name": "stop", "track_id": None, "held_ms": 1000 + i})
               for i in range(5)]
    _publish_and_settle(bus, events)

    assert pipeline.paused is True
    assert calls == []                       # stop never POSTs

# --- (b) follow_me onset: pins nothing, unpauses, POSTs bbox-center click ---

def test_follow_me_onset_posts_mono_click_at_bbox_center_and_unpauses():
    bus = EventBus()
    pipeline = StubPipeline()
    pipeline.paused = True
    reactor, calls = _start(bus, pipeline)

    track = {"id": 7, "label": "person", "conf": 0.9,
             "x": 100.0, "y": 50.0, "w": 80.0, "h": 160.0, "coasting": False}
    events = [snap(gesture={"name": None, "track_id": None, "held_ms": 0}),
              snap(gesture={"name": "follow_me", "track_id": 7, "held_ms": 1000},
                   tracks=[track])]
    _publish_and_settle(bus, events)

    assert calls == ["http://mono.example:8098/api/mono_click?x=140&y=130"]
    assert pipeline.paused is False
    assert pipeline.set_pinned_calls == [None]

# --- (c) point_* : no action ------------------------------------------------

def test_point_right_takes_no_action():
    bus = EventBus()
    pipeline = StubPipeline()
    reactor, calls = _start(bus, pipeline)

    events = [snap(gesture={"name": None, "track_id": None, "held_ms": 0}),
              snap(gesture={"name": "point_right", "track_id": 3, "held_ms": 1000})]
    _publish_and_settle(bus, events)

    assert calls == []
    assert pipeline.paused is False
    assert pipeline.set_pinned_calls == []

# --- (d) malformed events: ignored, no crash --------------------------------

def test_malformed_events_are_ignored_without_crashing():
    bus = EventBus()
    pipeline = StubPipeline()
    reactor, calls = _start(bus, pipeline)

    events = [
        {"v": 2, "tracks": [], "frame": {"width": 1280, "height": 720}},   # no "gesture" key
        snap(gesture={"track_id": 1, "held_ms": 1000}),                    # no "name" key
        snap(gesture={"name": "dance", "track_id": 1, "held_ms": 1000}),   # unknown name
        snap(gesture=None),                                                # gesture explicitly null
    ]
    _publish_and_settle(bus, events)

    assert calls == []
    assert pipeline.paused is False
    assert pipeline.set_pinned_calls == []
    assert reactor._thread.is_alive()   # the daemon thread survived all of the above

# --- controller-sanctioned addition: name-without-id hazard (Task 3 contract,
# release-hysteresis window up to ~400ms) must be a no-op, not a crash/POST ---

def test_follow_me_onset_with_null_track_id_takes_no_action():
    bus = EventBus()
    pipeline = StubPipeline()
    reactor, calls = _start(bus, pipeline)

    events = [snap(gesture={"name": None, "track_id": None, "held_ms": 0}),
              snap(gesture={"name": "follow_me", "track_id": None, "held_ms": 1000})]
    _publish_and_settle(bus, events)

    assert calls == []
    assert pipeline.paused is False
    assert pipeline.set_pinned_calls == []

def test_follow_me_onset_with_track_id_absent_from_tracks_takes_no_action():
    bus = EventBus()
    pipeline = StubPipeline()
    reactor, calls = _start(bus, pipeline)

    events = [snap(gesture={"name": None, "track_id": None, "held_ms": 0}),
              snap(gesture={"name": "follow_me", "track_id": 99, "held_ms": 1000},
                   tracks=[])]   # track_id 99 not present in tracks[]
    _publish_and_settle(bus, events)

    assert calls == []
    assert pipeline.paused is False
    assert pipeline.set_pinned_calls == []

# --- onset-only semantics: a repeated identical gesture across events must
# not re-fire (covered implicitly by (a)'s 5-repeat, made explicit for follow_me)

def test_repeated_follow_me_with_same_track_only_posts_once():
    bus = EventBus()
    pipeline = StubPipeline()
    reactor, calls = _start(bus, pipeline)

    track = {"id": 2, "label": "person", "conf": 0.9,
             "x": 0.0, "y": 0.0, "w": 40.0, "h": 100.0, "coasting": False}
    events = [snap(gesture={"name": None, "track_id": None, "held_ms": 0})]
    events += [snap(gesture={"name": "follow_me", "track_id": 2, "held_ms": 1000 + i},
                    tracks=[track]) for i in range(4)]
    _publish_and_settle(bus, events)

    assert len(calls) == 1

# --- review finding 1(a): a track_id change mid-hold (mono handoff arbitration,
# e.g. bystander takeover) re-fires even though `name` stays "follow_me" -------

def test_follow_me_track_id_change_mid_hold_refires_at_new_track_center():
    bus = EventBus()
    pipeline = StubPipeline()
    reactor, calls = _start(bus, pipeline)

    track_a = {"id": 7, "label": "person", "conf": 0.9,
               "x": 100.0, "y": 50.0, "w": 80.0, "h": 160.0, "coasting": False}
    track_b = {"id": 9, "label": "person", "conf": 0.85,
               "x": 300.0, "y": 40.0, "w": 60.0, "h": 140.0, "coasting": False}
    events = [snap(gesture={"name": None, "track_id": None, "held_ms": 0}),
              snap(gesture={"name": "follow_me", "track_id": 7, "held_ms": 1000},
                   tracks=[track_a, track_b]),
              snap(gesture={"name": "follow_me", "track_id": 9, "held_ms": 1400},
                   tracks=[track_a, track_b])]
    _publish_and_settle(bus, events)

    assert len(calls) == 2
    assert calls[0] == "http://mono.example:8098/api/mono_click?x=140&y=130"
    assert calls[1] == "http://mono.example:8098/api/mono_click?x=330&y=110"   # track 9's center

# --- review finding 1(b): a skipped attempt (null track_id) must not consume
# the onset — the action still fires once a valid id shows up on the same hold

def test_follow_me_null_id_then_valid_id_on_same_hold_fires_once():
    bus = EventBus()
    pipeline = StubPipeline()
    reactor, calls = _start(bus, pipeline)

    track = {"id": 5, "label": "person", "conf": 0.9,
             "x": 20.0, "y": 10.0, "w": 40.0, "h": 100.0, "coasting": False}
    events = [snap(gesture={"name": None, "track_id": None, "held_ms": 0}),
              snap(gesture={"name": "follow_me", "track_id": None, "held_ms": 1000}),   # skipped
              snap(gesture={"name": "follow_me", "track_id": 5, "held_ms": 1050},        # id arrives
                   tracks=[track])]
    _publish_and_settle(bus, events)

    assert len(calls) == 1
    assert calls[0] == "http://mono.example:8098/api/mono_click?x=40&y=60"
