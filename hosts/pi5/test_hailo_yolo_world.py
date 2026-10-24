"""Unit tests for the hardware-free parts of hosts/pi5/hailo_yolo_world.py.

The HEF/NPU/CLIP paths need the bench Pi; the two pure functions below carry the logic that is
easy to get silently wrong (80-slot padding, and the NMS-by-class -> letterbox-inverse decode),
so they run on x86 CI. Numbers here are constructed, NOT measured — see bench/benchlog.md.
"""
import numpy as np
import pytest

from hailo_yolo_world import (EMB_DIM, N_SLOTS, decode_nms_by_class, fill_slots,
                              set_prompt_checked)


def _emb(n, val=0.1):
    return np.full((n, EMB_DIM), val, np.float32)


def test_fill_slots_pads_to_the_compiled_slot_count_with_zeros():
    out = fill_slots(["pen", "keys"], _emb(2))
    assert out.shape == (N_SLOTS, EMB_DIM)
    assert np.allclose(out[:2], 0.1)
    assert not out[2:].any()            # unused slots are ZERO — the measured-better choice


def test_fill_slots_can_pad_with_a_background_embedding():
    bg = np.full(EMB_DIM, -0.2, np.float32)
    out = fill_slots(["pen"], _emb(1), bg)
    assert np.allclose(out[1:], -0.2)


def test_fill_slots_accepts_exactly_the_slot_count():
    assert fill_slots([str(i) for i in range(N_SLOTS)], _emb(N_SLOTS)).shape == (N_SLOTS, EMB_DIM)


@pytest.mark.parametrize("labels, emb", [
    ([], _emb(0)),                                   # no prompt at all
    ([str(i) for i in range(N_SLOTS + 1)], _emb(N_SLOTS + 1)),   # more prompts than slots
    (["pen"], _emb(2)),                              # label/embedding count mismatch
    (["pen"], np.zeros((1, 8), np.float32)),         # wrong embedding width
])
def test_fill_slots_rejects_bad_input(labels, emb):
    with pytest.raises(ValueError):
        fill_slots(labels, emb)


def _by_class(rows_per_class):
    """Build a HailoRT-shaped output: one (n,5) array per slot, padded out to N_SLOTS."""
    out = [np.asarray(r, np.float32).reshape(-1, 5) for r in rows_per_class]
    return out + [np.zeros((0, 5), np.float32)] * (N_SLOTS - len(out))


def test_decode_maps_a_letterboxed_box_back_to_original_pixels():
    # 1280x720 -> 640x640 letterbox: scale 0.5, pad_x 0, pad_y 460//2 = 140
    r, px, py = 0.5, 0, 140
    # a box covering x 100..500, y 100..300 in the ORIGINAL image
    x0, y0, x1, y1 = 100 * r + px, 100 * r + py, 500 * r + px, 300 * r + py
    rows = [[y0 / 640, x0 / 640, y1 / 640, x1 / 640, 0.9]]
    d = decode_nms_by_class(_by_class([rows]), ["pen"], 1280, 720, (r, px, py))
    assert len(d) == 1
    assert d[0].label == "pen" and d[0].class_id == 0
    assert d[0].conf == pytest.approx(0.9)
    assert (d[0].x, d[0].y, d[0].w, d[0].h) == pytest.approx((100, 100, 400, 200), abs=1e-3)


def test_decode_ignores_padded_slots_beyond_the_prompt_list():
    junk = [[0.0, 0.0, 1.0, 1.0, 0.5]]
    by_class = _by_class([[], [], junk, junk])       # hits in slots 2 and 3
    assert decode_nms_by_class(by_class, ["pen", "keys"], 640, 640, (1.0, 0, 0)) == []


def test_decode_applies_the_confidence_floor_and_sorts_by_score():
    rows_a = [[0.1, 0.1, 0.5, 0.5, 0.20], [0.2, 0.2, 0.6, 0.6, 0.05]]
    rows_b = [[0.0, 0.0, 0.4, 0.4, 0.80]]
    d = decode_nms_by_class(_by_class([rows_a, rows_b]), ["pen", "keys"], 640, 640, (1.0, 0, 0),
                            conf=0.1)
    assert [x.label for x in d] == ["keys", "pen"]   # 0.05 row dropped, rest sorted desc


def test_decode_clamps_to_the_frame_and_drops_degenerate_boxes():
    rows = [[-0.5, -0.5, 1.5, 1.5, 0.7],             # overshoots the frame -> clamped
            [0.5, 0.5, 0.5, 0.5, 0.7]]               # zero area -> dropped
    d = decode_nms_by_class(_by_class([rows]), ["pen"], 640, 640, (1.0, 0, 0))
    assert len(d) == 1
    assert (d[0].x, d[0].y, d[0].w, d[0].h) == pytest.approx((0, 0, 640, 640))


# --- set_prompt_checked: the live detector's vocabulary must never diverge from the reported one ---

class FakeDetector:
    """Enough of HailoYoloWorld to exercise the staging logic with no HEF and no NPU."""

    emb_shape = (1, N_SLOTS, EMB_DIM)

    def __init__(self):
        self.labels = []
        self._emb = np.zeros(self.emb_shape, np.float32)
        self.infer_raises = None
        self.infer_calls = 0

    def set_prompt(self, labels, embeddings, pad_embedding=None):
        full = fill_slots(labels, embeddings, pad_embedding)     # validates, may raise
        self._emb = np.ascontiguousarray(full.reshape(self.emb_shape), np.float32)
        self.labels = list(labels)

    def infer(self, img):
        self.infer_calls += 1
        if self.infer_raises is not None:
            raise self.infer_raises
        return []


def _world_in_mode(labels, val):
    d = FakeDetector()
    set_prompt_checked(d, labels, _emb(len(labels), val), probe="frame")
    return d


def test_checked_prompt_commits_on_success_and_probes_once():
    d = _world_in_mode(["pen", "keys"], 0.1)
    assert d.labels == ["pen", "keys"]
    assert d.infer_calls == 1
    assert np.allclose(d._emb.reshape(N_SLOTS, EMB_DIM)[:2], 0.1)


def test_failed_reprompt_leaves_the_live_vocabulary_EXACTLY_as_before():
    """The finding: a re-prompt whose probe infer fails must not leave the detector running the
    new, unconfirmed vocabulary while the caller keeps reporting the old one."""
    d = _world_in_mode(["pen", "keys"], 0.1)          # already in world mode, confirmed
    before_labels, before_emb = list(d.labels), d._emb.copy()
    reported = list(d.labels)                         # what _detector["prompt"] would still say

    d.infer_raises = RuntimeError("HW timeout on the probe frame")
    with pytest.raises(RuntimeError):
        set_prompt_checked(d, ["chair", "mug", "lamp"], _emb(3, 0.9), probe="frame")

    assert d.labels == before_labels                  # live vocabulary reverted
    assert np.array_equal(d._emb, before_emb)         # embedding tensor reverted bit-for-bit
    assert d.labels == reported                       # live state == reported state: no divergence


def test_failed_reprompt_reverts_on_bad_embeddings_too():
    d = _world_in_mode(["pen"], 0.1)
    before_labels, before_emb = list(d.labels), d._emb.copy()
    with pytest.raises(ValueError):                   # count mismatch, fails inside set_prompt
        set_prompt_checked(d, ["chair", "mug"], _emb(3, 0.9), probe="frame")
    assert d.labels == before_labels
    assert np.array_equal(d._emb, before_emb)
    assert d.infer_calls == 1                         # probe never ran for the failed attempt


def test_first_activation_failure_leaves_the_detector_unprompted():
    """Failing the very first world switch must leave .labels empty, so infer() refuses rather
    than silently running a half-applied vocabulary."""
    d = FakeDetector()
    d.infer_raises = RuntimeError("configure/probe failed")
    with pytest.raises(RuntimeError):
        set_prompt_checked(d, ["pen"], _emb(1, 0.1), probe="frame")
    assert d.labels == []
    assert not d._emb.any()


def test_checked_prompt_skips_the_probe_when_no_frame_is_available():
    d = FakeDetector()
    d.infer_raises = RuntimeError("must not be called")
    set_prompt_checked(d, ["pen"], _emb(1, 0.1), probe=None)   # no camera frame yet
    assert d.labels == ["pen"] and d.infer_calls == 0
