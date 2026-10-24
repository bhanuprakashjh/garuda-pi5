import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "interfaces"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from garuda_interfaces import FrameMeta
from garuda_interfaces.capture import ICaptureBackend
from stereo import plan_split, crop_row_major, per_view_metas, disparity_to_depth_mm, CropRect
from camarray_source import CamArraySource


def test_plan_split_stereo_pair():
    r = plan_split(2560, 800, 1, 2)
    assert r == [CropRect(0, 0, 1280, 800), CropRect(1280, 0, 1280, 800)]

def test_plan_split_quad_grid_and_row():
    assert len(plan_split(2560, 1600, 2, 2)) == 4
    assert plan_split(5120, 800, 1, 4)[3] == CropRect(3840, 0, 1280, 800)

def test_plan_split_rejects_non_divisible():
    with pytest.raises(ValueError):
        plan_split(1281, 800, 1, 2)   # odd width can't split into 2 equal tiles

def test_crop_row_major_extracts_correct_tile():
    # 4x2 mono frame: row0=[0,1,2,3], row1=[4,5,6,7]; split into two 2x2 tiles
    buf = bytes(range(8))
    left, right = plan_split(4, 2, 1, 2)
    assert list(crop_row_major(buf, 4, 1, left)) == [0, 1, 4, 5]
    assert list(crop_row_major(buf, 4, 1, right)) == [2, 3, 6, 7]

def test_per_view_metas_share_timestamp_distinct_sensor():
    base = FrameMeta(frame_id=7, t_capture_ns=111, clock_domain="mono",
                     sensor="ov9281.array", width=2560, height=800, pixel_format="MONO8")
    metas = per_view_metas(base, ["ov9281.cam0", "ov9281.cam1"], 1280, 800)
    assert [m.sensor for m in metas] == ["ov9281.cam0", "ov9281.cam1"]
    assert all(m.t_capture_ns == 111 and m.frame_id == 7 for m in metas)   # hardware-synced
    assert all(m.width == 1280 and m.height == 800 for m in metas)

def test_disparity_to_depth_mm():
    # f=1000px, B=60mm, d=30px -> Z = 1000*60/30 = 2000mm
    assert disparity_to_depth_mm(30, 1000, 60) == pytest.approx(2000.0)
    assert disparity_to_depth_mm(0, 1000, 60) == 0.0      # no match -> 0 (unknown)
    assert disparity_to_depth_mm(-5, 1000, 60) == 0.0


class _FakeBackend(ICaptureBackend):
    """Feeds one synthetic combined frame when started — no camera needed."""
    def __init__(self, meta, buf):
        self.meta, self.buf = meta, buf
        self.configured = None
    def configure(self, width, height, fps, pixel_format="RGB888", trigger_external=False):
        self.configured = (width, height, fps, pixel_format)
    def start(self, on_frame):
        on_frame(self.meta, memoryview(self.buf))
    def stop(self):
        pass

def test_camarray_source_splits_into_synced_views():
    meta = FrameMeta(frame_id=1, t_capture_ns=999, clock_domain="mono",
                     sensor="ov9281.array", width=4, height=2, pixel_format="MONO8")
    src = CamArraySource(_FakeBackend(meta, bytes(range(8))), rows=1, cols=2,
                         sensors=["ov9281.cam0", "ov9281.cam1"],
                         combined_w=4, combined_h=2, pixel_format="MONO8")
    got = []
    src.start(lambda metas, views: got.append((metas, views)))
    metas, views = got[0]
    assert src.backend.configured == (4, 2, 60.0, "MONO8")
    assert [m.sensor for m in metas] == ["ov9281.cam0", "ov9281.cam1"]
    assert all(m.t_capture_ns == 999 for m in metas)          # shared exposure
    assert list(bytes(views[0])) == [0, 1, 4, 5]              # left tile
    assert list(bytes(views[1])) == [2, 3, 6, 7]              # right tile

def test_camarray_source_rejects_wrong_sensor_count():
    with pytest.raises(ValueError):
        CamArraySource(_FakeBackend(None, b""), rows=1, cols=2,
                       sensors=["only-one"], combined_w=4, combined_h=2)
