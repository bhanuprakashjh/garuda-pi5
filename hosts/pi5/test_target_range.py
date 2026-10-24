import pytest
from target_range import Detection, z_from_disparity, select_target, range_target


def det(label, conf, x=0, y=0, w=10, h=10, cid=0):
    return Detection(cid, label, conf, x, y, w, h)


def test_z_from_disparity_known():
    # f=1000px, B=60mm, disp=20px -> 1000*60/20 = 3000mm = 3.0m
    assert z_from_disparity(1000.0, 60.0, 20.0) == pytest.approx(3.0)


@pytest.mark.parametrize("bad", [0.0, -5.0, None])
def test_z_from_disparity_rejects_nonpositive(bad):
    with pytest.raises(ValueError):
        z_from_disparity(1000.0, 60.0, bad)


def test_select_target_picks_max_conf_and_counts():
    dets = [det("person", 0.6), det("person", 0.9), det("car", 0.8)]
    best, count = select_target(dets, "person")
    assert best.conf == 0.9 and count == 2


def test_select_target_absent():
    best, count = select_target([det("car", 0.8)], "person")
    assert best is None and count == 0


def test_range_target_ok():
    r = range_target(1000.0, 60.0, [det("person", 0.9)], "person", lambda d: 20.0)
    assert r.valid and r.reason == "ok" and r.z_m == pytest.approx(3.0) and r.count == 1


def test_range_target_no_class():
    r = range_target(1000.0, 60.0, [det("car", 0.8)], "person", lambda d: 20.0)
    assert (not r.valid) and r.reason == "no_class"


def test_range_target_bad_disparity():
    r = range_target(1000.0, 60.0, [det("person", 0.9)], "person", lambda d: None)
    assert (not r.valid) and r.reason == "bad_disparity" and r.count == 1
