"""Image-based visual servoing for mono subject-following, with a load-bearing
safety gate: a lost / low-confidence / stale / implausible target yields a HOLD
(zero velocities, valid=False) — never a chase. This is the odometry
covariance/validity discipline applied to following."""
from __future__ import annotations
import sys, pathlib
from dataclasses import dataclass
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "interfaces"))
from garuda_interfaces import FollowCommand


@dataclass
class FollowConfig:
    desired_height_frac: float = 0.5   # target bbox should fill ~half the frame height
    yaw_gain: float = 2.0              # rad/s per unit horizontal error
    forward_gain: float = 3.0         # m/s per unit (desired-actual) height error
    vertical_gain: float = 1.0        # m/s per unit vertical error
    deadzone: float = 0.03            # ignore errors this small (fraction of frame)
    max_yaw_rate: float = 0.8
    max_forward_vel: float = 2.0
    max_vertical_vel: float = 1.0
    min_confidence: float = 0.35
    max_stale_frames: int = 5
    min_height_frac: float = 0.05
    max_height_frac: float = 0.95


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _dz(err, dz):
    return 0.0 if abs(err) < dz else err


def compute(target, frame_wh, cfg, t_ns=0) -> FollowCommand:
    def hold(reason):
        return FollowCommand(t_ns=t_ns, target_id=(target.id if target else None),
                             yaw_rate=0.0, forward_vel=0.0, vertical_vel=0.0,
                             valid=False, reason=reason)
    if target is None:
        return hold("no target")
    if target.confidence < cfg.min_confidence:
        return hold(f"low confidence {target.confidence:.2f}")
    if target.time_since_update > cfg.max_stale_frames:
        return hold(f"stale target ({target.time_since_update} frames)")
    x, y, w, h = target.bbox
    if h < cfg.min_height_frac or h > cfg.max_height_frac:
        return hold(f"implausible size h={h:.2f}")

    cx, cy = x + w / 2.0, y + h / 2.0
    ex = _dz(cx - 0.5, cfg.deadzone)          # +: target right of center
    ey = _dz(cy - 0.5, cfg.deadzone)          # +: target below center
    eh = cfg.desired_height_frac - h          # +: target too small (far) -> go forward

    yaw = _clamp(cfg.yaw_gain * ex, -cfg.max_yaw_rate, cfg.max_yaw_rate)
    fwd = _clamp(cfg.forward_gain * eh, -cfg.max_forward_vel, cfg.max_forward_vel)
    vert = _clamp(-cfg.vertical_gain * ey, -cfg.max_vertical_vel, cfg.max_vertical_vel)
    return FollowCommand(t_ns=t_ns, target_id=target.id, yaw_rate=yaw,
                         forward_vel=fwd, vertical_vel=vert, valid=True, reason="tracking")
