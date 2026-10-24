"""World→image projection for forward-facing pinhole camera.
Projects a world target into a normalized-image bbox for the drone's camera.
Used by sitl_followme.py and test harnesses.
"""
import math


def project(target_world, drone_pos, drone_yaw, hfov=1.2, vfov=0.9, target_size=6.0):
    """Project a world target into a normalized-image bbox.

    Args:
        target_world: (tx, ty) in world frame
        drone_pos: (dx, dy) in world frame
        drone_yaw: yaw angle in radians (0 = +x)
        hfov: horizontal field of view in radians
        vfov: vertical field of view in radians (unused in this implementation)
        target_size: effective height of target in world units

    Returns:
        (x, y, w, h) normalized bbox, or None if target is behind/outside frustum
    """
    tx, ty = target_world
    dx, dy = drone_pos

    # Vector from drone to target
    rel_x = tx - dx
    rel_y = ty - dy

    # Distance and bearing
    rng = math.hypot(rel_x, rel_y)
    bearing = math.atan2(rel_y, rel_x) - drone_yaw

    # Normalize bearing to [-pi, pi]
    bearing = (bearing + math.pi) % (2 * math.pi) - math.pi

    # Check visibility: target must be within horizontal FOV
    if abs(bearing) >= hfov / 2:
        return None

    # Horizontal center in normalized image (0..1)
    cx = 0.5 + bearing / hfov

    # Check bounds
    if cx < 0 or cx > 1:
        return None

    # Vertical height from distance (farther -> smaller)
    h = min(0.95, target_size / max(rng, 0.1) * 0.1)

    # Return normalized bbox (x, y, w, h)
    # x is left edge, w is width (constant at 0.2 for centered appearance)
    # y is top edge, h is height
    x = cx - 0.1
    y = 0.5 - h / 2
    w = 0.2

    return (x, y, w, h)
