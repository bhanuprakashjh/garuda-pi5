import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from garuda_interfaces import Track
from target_selector import select

def trk(id, label, conf):
    return Track(id=id, class_id=0, label=label, bbox=(0.4, 0.4, 0.2, 0.2), confidence=conf)

def test_auto_picks_highest_confidence_of_class():
    tracks = [trk(1, "person", 0.5), trk(2, "person", 0.9), trk(3, "car", 0.99)]
    assert select(tracks, follow_class="person").id == 2

def test_pinned_id_wins_when_present():
    tracks = [trk(1, "person", 0.5), trk(2, "person", 0.9)]
    assert select(tracks, follow_class="person", pinned_id=1).id == 1

def test_pinned_absent_holds_returns_none():
    """A pinned target that is no longer present must HOLD (return None), not
    silently fall back to auto-selecting a different subject."""
    tracks = [trk(2, "person", 0.9)]
    assert select(tracks, follow_class="person", pinned_id=1) is None

def test_none_when_no_class_match():
    assert select([trk(3, "car", 0.99)], follow_class="person") is None
    assert select([], follow_class="person") is None

def test_pinned_overrides_class_filter():
    """Pinned track is returned regardless of follow_class."""
    assert select([trk(1, "car", 0.9)], follow_class="person", pinned_id=1).id == 1

def test_no_pin_still_auto_selects():
    """Guard against over-correcting: with no pin set (pinned_id=None), auto
    selection of the highest-confidence follow_class track is unaffected."""
    tracks = [trk(1, "person", 0.5), trk(2, "person", 0.9), trk(3, "car", 0.99)]
    assert select(tracks, follow_class="person", pinned_id=None).id == 2
