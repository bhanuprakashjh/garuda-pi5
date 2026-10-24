import pytest
np = pytest.importorskip("numpy")
from hailo_detect import decode_nms


def test_decode_nms_pixels_and_threshold():
    labels = ["person", "car"]
    raw = [
        np.array([[0.10, 0.20, 0.50, 0.40, 0.90],   # person, conf 0.90
                  [0.00, 0.00, 0.10, 0.10, 0.20]]),  # person, conf 0.20 -> dropped
        np.array([[0.30, 0.30, 0.60, 0.70, 0.80]]),  # car, conf 0.80
    ]
    dets = decode_nms(raw, labels, in_w=640, in_h=480, conf_thresh=0.4)
    assert len(dets) == 2
    person = [d for d in dets if d.label == "person"][0]
    assert person.conf == pytest.approx(0.90)
    # xmin*w=0.20*640=128, ymin*h=0.10*480=48, w=(0.40-0.20)*640=128, h=(0.50-0.10)*480=192
    assert (round(person.x), round(person.y), round(person.w), round(person.h)) == (128, 48, 128, 192)


def test_decode_nms_empty_and_missing_classes():
    # Empty per-class arrays and a None slot must be skipped without error.
    labels = ["person", "car", "dog"]
    raw = [np.zeros((0, 5)), None, np.array([[0.1, 0.1, 0.2, 0.2, 0.95]])]
    dets = decode_nms(raw, labels, in_w=100, in_h=100, conf_thresh=0.4)
    assert len(dets) == 1 and dets[0].label == "dog" and dets[0].class_id == 2


def test_decode_nms_class_id_beyond_labels_is_safe():
    # More classes present than labels supplied -> label falls back to the id string, no crash.
    raw = [np.zeros((0, 5)), np.array([[0.1, 0.1, 0.2, 0.2, 0.9]])]
    dets = decode_nms(raw, labels=["person"], in_w=100, in_h=100, conf_thresh=0.4)
    assert len(dets) == 1 and dets[0].label == "1"
