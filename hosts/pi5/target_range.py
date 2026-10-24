"""Pure association + metric geometry for stereo target ranging. No cv2/Hailo:
the disparity source is injected as `disparity_fn` so this is fully unit-tested."""
from collections import namedtuple

Detection = namedtuple("Detection", "class_id label conf x y w h")   # x,y,w,h in pixels
RangeResult = namedtuple("RangeResult", "z_m conf count valid reason")


def z_from_disparity(focal_px, baseline_mm, disparity_px):
    """Metres from a rectified-pixel disparity. Z_mm = f*B/d."""
    if disparity_px is None or disparity_px <= 0:
        raise ValueError(f"disparity must be > 0, got {disparity_px!r}")
    return (focal_px * baseline_mm / disparity_px) / 1000.0


def select_target(detections, target_class):
    """(highest-confidence detection of target_class, count of that class)."""
    matches = [d for d in detections if d.label == target_class]
    if not matches:
        return None, 0
    return max(matches, key=lambda d: d.conf), len(matches)


def range_target(focal_px, baseline_mm, detections, target_class, disparity_fn):
    """Select the target, sample its disparity, convert to metres.
    reason in {ok, no_class, bad_disparity}. count>1 does NOT invalidate."""
    best, count = select_target(detections, target_class)
    if best is None:
        return RangeResult(None, 0.0, 0, False, "no_class")
    disp = disparity_fn(best)
    if disp is None or disp <= 0:
        return RangeResult(None, best.conf, count, False, "bad_disparity")
    return RangeResult(z_from_disparity(focal_px, baseline_mm, disp),
                       best.conf, count, True, "ok")
