import time
try:
    import cv2
except ImportError:
    import pytest
    pytest.skip("Face adapter tests run with Pi OpenCV", allow_module_level=True)
from mono_faces import associate_face, FaceWorker


def test_face_association_rejects_ambiguous_bystander_boxes():
    face = [30, 20, 40, 40]
    person = (7, 'person', 10, 10, 100, 180, .9, False)
    assert associate_face(face, [person]) == 7
    assert associate_face(face, [person, (8, *person[1:])]) is None
    assert associate_face(face, [(7, *person[1:7], True)]) is None


def test_face_snapshot_rejects_stale_other_epoch_and_disabled_results():
    worker = FaceWorker.__new__(FaceWorker)
    worker.enabled = True
    worker.result = ({7: {'sample': 'a', 'embedding': [0]*128}}, time.monotonic(), 'epoch')
    assert 7 in worker.snapshot('epoch')
    assert worker.snapshot('other') == {}
    worker.result = (worker.result[0], time.monotonic()-3, 'epoch')
    assert worker.snapshot('epoch') == {}
    worker.enabled = False
    assert worker.snapshot('epoch') == {}
