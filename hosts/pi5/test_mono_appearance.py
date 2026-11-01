import numpy as np
from types import SimpleNamespace
try:
    import cv2
except ImportError:
    import pytest
    pytest.skip("OpenCV descriptor checks run on the Pi", allow_module_level=True)
from mono_appearance import describe_people


def track(**kw):
    return SimpleNamespace(**dict(dict(track_id=7, cls='person', time_since_update=0,
                                      conf=.9, bbox=(20, 10, 80, 180)), **kw))


def test_descriptor_is_normalized_and_tracks_colour():
    a = np.full((200, 120, 3), (10, 30, 200), dtype=np.uint8)
    b = np.full((200, 120, 3), (200, 30, 10), dtype=np.uint8)
    x = np.array(describe_people(a, [track()])[7])
    y = np.array(describe_people(b, [track()])[7])
    assert len(x) == 128 and abs(np.dot(x, x)-1) < .0001
    assert np.dot(x, y) < .1


def test_coasting_low_confidence_and_other_classes_have_no_descriptor():
    img = np.zeros((200, 120, 3), dtype=np.uint8)
    for t in [track(time_since_update=1), track(conf=.2), track(cls='chair'),
              track(bbox=(-10, 0, 80, 180)), track(bbox=(0, 0, 10, 10))]:
        assert describe_people(img, [t]) == {}


def test_resize_keeps_uniform_clothing_descriptor():
    img = np.full((400, 240, 3), 170, dtype=np.uint8)
    assert describe_people(img, [track()])[7] == describe_people(img, [track(bbox=(40, 20, 160, 360))])[7]
