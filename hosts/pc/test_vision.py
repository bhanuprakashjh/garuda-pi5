"""Camera/replay contract tests; no running Gazebo or inference model required."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from types import SimpleNamespace
import numpy as np
from gazebo_pixels import depth_range, rgb_message
from vision import Replay

def test_padded_rgb_rows_and_bad_pixel_format():
    msg=SimpleNamespace(width=2,height=1,step=8,pixel_format_type=3,data=bytes(range(8)))
    assert rgb_message(msg).tolist()==[[[0,1,2],[3,4,5]]]
    msg.pixel_format_type=13
    try:rgb_message(msg)
    except ValueError:pass
    else:raise AssertionError('Depth must not decode as RGB')

def test_depth_rejects_background_mixture_and_missing_depth():
    box=dict(x=100,y=100,w=100,h=200)
    depth=np.full((720,1280),3.,dtype=np.float32)
    assert depth_range(depth,box)==3
    assert depth_range(None,box) is None
    depth[150:185]=20
    assert depth_range(depth,box) is None

def test_replay_end_is_finite_and_does_not_loop(tmp_path):
    import cv2
    cv2.imwrite(str(tmp_path/'000001.jpg'),np.zeros((8,8,3),dtype=np.uint8))
    r=Replay(dict(path=str(tmp_path),kind='images',fps=30))
    assert r.read().shape==(8,8,3)
    assert r.read() is None and r.done


def test_packaged_sample_is_labeled_synthetic_video(tmp_path, monkeypatch):
    import vision
    monkeypatch.setattr(vision, 'ROOT', tmp_path)
    (tmp_path / 'samples').mkdir()
    (tmp_path / 'samples/playback-check.avi').write_bytes(b'fixture')
    entry = vision.catalog(tmp_path / 'no-dataset')['garuda-playback-check']
    assert entry['kind'] == 'video'
    assert 'not a detection benchmark' in entry['label']
