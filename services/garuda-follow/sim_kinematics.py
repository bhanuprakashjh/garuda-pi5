"""A minimal 2.5D drone + world-target simulator used to prove the follow
controller converges in a true closed loop — no PX4 needed, deterministic, fast.
The drone yaws and translates per the controller's command; the target is
projected into its image plane (bearing -> horizontal bbox position, range ->
bbox height). The SITL harness (sitl_followme.py) does the same against real PX4."""
from __future__ import annotations
import math, sys, pathlib
from dataclasses import dataclass
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "interfaces"))
from garuda_interfaces import Track


@dataclass
class DroneState:
    x: float
    y: float
    z: float
    yaw: float                      # rad, 0 = +x


def step(state, cmd, dt):
    yaw = state.yaw + cmd.yaw_rate * dt
    x = state.x + cmd.forward_vel * math.cos(yaw) * dt
    y = state.y + cmd.forward_vel * math.sin(yaw) * dt
    z = state.z + cmd.vertical_vel * dt
    return DroneState(x=x, y=y, z=z, yaw=yaw)


def target_bbox(state, target_xy, cfg, hfov=1.2, target_size=6.0):
    """Project a world target into a normalized-image bbox. If the target is
    outside the horizontal FOV (incl. behind), return a Track flagged stale so
    the controller holds."""
    tx, ty = target_xy
    dx, dy = tx - state.x, ty - state.y
    rng = math.hypot(dx, dy)
    bearing = math.atan2(dy, dx) - state.yaw
    # normalize bearing to [-pi, pi]
    bearing = (bearing + math.pi) % (2 * math.pi) - math.pi
    cx = 0.5 + bearing / hfov       # 0..1 across the horizontal FOV
    h = min(0.95, target_size / max(rng, 0.1) * 0.1)   # farther -> smaller bbox
    visible = abs(bearing) < hfov / 2 and 0 <= cx <= 1
    tsu = 0 if visible else cfg.max_stale_frames + 1
    return Track(id=1, class_id=0, label="person",
                 bbox=(cx - 0.1, 0.5 - h / 2, 0.2, h), confidence=0.9,
                 hits=1, age=1, time_since_update=tsu)
