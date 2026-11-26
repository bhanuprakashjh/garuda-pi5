"""Colourise an aligned stereo depth image for viewing. numpy only; no cv2, no device.

This is a picture for a person to look at, not a measurement: colours are quantised, the
image is downsampled, and the scale is clipped at max_mm. Pixels with no depth (0) are black.
Near is warm (red/yellow), far is cool (blue).
"""
import numpy as np
import json
import struct

NO_DEPTH_BGR = (0, 0, 0)
BEYOND_BGR = (96, 48, 48)            # farther than the scale: dull blue-grey, still "has depth"


def _lut():
    """256-entry BGR ramp, fully saturated end to end:
    nearest = red -> yellow -> green -> cyan -> blue = farthest."""
    t = np.linspace(0.0, 1.0, 256)
    r = np.clip(2.0 - 4.0 * t, 0, 1)                 # 1 until a quarter, gone by half
    g = np.clip(np.minimum(4.0 * t, 4.0 - 4.0 * t), 0, 1)
    b = np.clip(4.0 * t - 2.0, 0, 1)                 # starts at half, 1 from three quarters
    return (np.stack([b, g, r], axis=1) * 255).astype(np.uint8)


LUT = _lut()


def observed_cells(depth_mm, step):
    """Pick the first measured pixel in each cell, retaining its row-major offset."""
    h, w = depth_mm.shape
    if step == 1 or not depth_mm.size:
        grid = depth_mm[::step, ::step]
        return grid, np.zeros_like(grid, dtype=np.uint8)
    sy, sx = min(step, h), min(step, w)
    # Preserve partial cells around the bottom and right image boundaries.
    if h % sy or w % sx:
        depth_mm = np.pad(depth_mm, ((0, -h % sy), (0, -w % sx)))
    rows, cols = depth_mm.shape[0] // sy, depth_mm.shape[1] // sx
    cells = depth_mm.reshape(rows, sy, cols, sx).transpose(0, 2, 1, 3).reshape(rows, cols, -1)
    offsets = np.argmax(cells != 0, axis=2)
    depths = np.take_along_axis(cells, offsets[:, :, None], axis=2)[:, :, 0]
    if sx != step:
        offsets = offsets // sx * step + offsets % sx
    return depths, offsets


def colourise(depth_mm, max_mm=6000, step=2):
    """HxW uint16 mm (0 = no depth) -> (H/step)x(W/step)x3 uint8 BGR."""
    if not isinstance(depth_mm, np.ndarray) or depth_mm.ndim != 2 or depth_mm.dtype != np.uint16:
        raise ValueError("depth_mm must be a 2-D uint16 array of millimetres")
    if not (isinstance(step, int) and step >= 1) or not max_mm > 0:
        raise ValueError("step must be a positive integer and max_mm positive")
    d = depth_mm if step == 1 else observed_cells(depth_mm, step)[0]
    idx = np.minimum(d.astype(np.uint32) * 255 // int(max_mm), 255).astype(np.uint8)
    out = LUT[idx]
    out[d > max_mm] = BEYOND_BGR
    out[d == 0] = NO_DEPTH_BGR
    return out


def draw_box(img, roi, step=2, colour=(255, 255, 255)):
    """Outline a full-resolution (x0, y0, x1, y1) region on a colourised image, in place.
    Malformed or out-of-image regions are ignored."""
    try:
        x0, y0, x1, y1 = (int(v) // step for v in roi)
    except (TypeError, ValueError):
        return img
    h, w = img.shape[:2]
    x0, x1 = max(0, x0), min(w - 1, x1)
    y0, y1 = max(0, y0), min(h - 1, y1)
    if x1 <= x0 or y1 <= y0:
        return img
    img[y0, x0:x1 + 1] = colour
    img[y1, x0:x1 + 1] = colour
    img[y0:y1 + 1, x0] = colour
    img[y0:y1 + 1, x1] = colour
    return img


CLOUD_MAGIC = b"GCL1"


def cloud_payload(depth_mm, thumb_bgr, step=8, geometry=None):
    """Bytes for the browser's 3-D view of ONE pair: depth and colour on the same coarse grid.

    Layout: b"GCL1", uint16 LE width, uint16 LE height, then width*height uint16 LE depth in
    millimetres (0 = no depth), then width*height*3 uint8 BGR. The browser back-projects it.
    The colour comes from the same pair as the depth; if the two grids differ in shape the
    payload is refused rather than paired approximately."""
    if not isinstance(depth_mm, np.ndarray) or depth_mm.ndim != 2 or depth_mm.dtype != np.uint16:
        raise ValueError("depth_mm must be a 2-D uint16 array of millimetres")
    d = np.ascontiguousarray(depth_mm[::step, ::step], dtype="<u2")
    if (not isinstance(thumb_bgr, np.ndarray) or thumb_bgr.dtype != np.uint8
            or thumb_bgr.shape != d.shape + (3,)):
        raise ValueError("colour grid does not match the depth grid")
    h, w = d.shape
    if geometry is not None:
        header=json.dumps(dict(geometry=geometry,step=step,width=w,height=h), allow_nan=False, separators=(',',':')).encode()
        if len(header)>16384: raise ValueError('cloud metadata too large')
        return b'GCL2'+struct.pack('<I',len(header))+header+d.tobytes()+np.ascontiguousarray(thumb_bgr).tobytes()
    return CLOUD_MAGIC + int(w).to_bytes(2, "little") + int(h).to_bytes(2, "little") \
        + d.tobytes() + np.ascontiguousarray(thumb_bgr).tobytes()


def observed_cloud_payload(depth_mm, rgb, step, geometry):
    """GCL3 carries a pixel offset per cell in addition to GCL2's depth and BGR."""
    if step not in (2, 4, 8) or depth_mm.dtype != np.uint16 or depth_mm.ndim != 2:
        raise ValueError("invalid depth grid or cloud step")
    if rgb.shape != depth_mm.shape + (3,) or rgb.dtype != np.uint8:
        raise ValueError("colour and depth grids differ")
    d, offsets = observed_cells(depth_mm, step)
    h, w = d.shape
    y = np.arange(h)[:, None] * step + offsets // step
    x = np.arange(w)[None, :] * step + offsets % step
    header = json.dumps(dict(geometry=geometry, step=step, width=w, height=h),
                        allow_nan=False, separators=(',', ':')).encode()
    if len(header) > 16384:
        raise ValueError("cloud metadata too large")
    return b''.join((b'GCL3', struct.pack('<I', len(header)), header, d.astype('<u2', copy=False).tobytes(),
                     rgb[y, x].tobytes(), offsets.astype(np.uint8).tobytes()))
