import sys
import pathlib

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from stereo_calibrate import make_object_points


def test_object_points_shape_and_grid():
    cols, rows, sq = 11, 8, 33.5
    objp = make_object_points(cols, rows, sq)
    assert objp.shape == (cols * rows, 3)
    assert np.allclose(objp[:, 2], 0.0)            # planar board
    # first two points are one column apart (row-major over cols first)
    assert np.allclose(objp[0], [0.0, 0.0, 0.0])
    assert np.allclose(objp[1], [sq, 0.0, 0.0])    # step of square_size in x
    # spans the full grid extent
    assert np.isclose(objp[:, 0].max(), (cols - 1) * sq)
    assert np.isclose(objp[:, 1].max(), (rows - 1) * sq)


def test_square_size_is_pure_scale():
    a = make_object_points(9, 6, 10.0)
    b = make_object_points(9, 6, 20.0)
    assert np.allclose(b, a * 2.0)                 # only the scale changes
