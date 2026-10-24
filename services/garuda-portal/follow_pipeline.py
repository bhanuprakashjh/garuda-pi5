"""FollowPipeline — the portal's single entry point onto the pure
garuda-follow modules (Tracker + select + compute).

garuda-follow and interfaces are sibling packages, not installed on the
portal's Python path, so we add both here (once, at import time) the same
way tracker.py/follow_controller.py add the interfaces dir for themselves.
Any caller (portal.py, this module's own tests) can then simply
`from follow_pipeline import FollowPipeline`.

The pipeline holds state across frames (the Tracker's track table and the
pinned-target id) — one instance per inference session, called once per
`detections` event.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# garuda-follow and interfaces are sibling packages we add to sys.path here, but
# their location differs by environment. In the repo they live two levels up
# (<root>/services/garuda-follow, <root>/interfaces). The Pi deploy
# (hosts/pi5/install.sh) flattens each service into /opt/garuda/<name>, so the
# same siblings sit ONE level up (/opt/garuda/garuda-follow, /opt/garuda/interfaces)
# — parents[2] there is /opt, which has neither. Probe both layouts and use
# whichever actually holds the modules, so this identical file imports cleanly on
# the dev PC (tests) and on the deployed Pi. Falls back to the repo layout so a
# genuine miss raises an ImportError naming an expected path, not a silent skip.
_HERE = Path(__file__).resolve()
_LAYOUTS = (
    (_HERE.parents[2] / "services" / "garuda-follow", _HERE.parents[2] / "interfaces"),  # repo
    (_HERE.parents[1] / "garuda-follow", _HERE.parents[1] / "interfaces"),               # Pi deploy
)
_follow_dir, _iface_dir = next(
    ((f, i) for f, i in _LAYOUTS if f.is_dir() and i.is_dir()), _LAYOUTS[0])
for _p in (str(_follow_dir), str(_iface_dir)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tracker import Tracker
from target_selector import select
from follow_controller import compute, FollowConfig
from garuda_interfaces import FollowCommand

# Tracker.update/iou index every detection dict by these keys directly
# (d["x"], d["h"], d["class_id"], ...) with no .get() guard of its own. The
# wire format is a subprocess JSONL hop from garuda-inference, so a single
# malformed/renamed/partial detection (schema drift, truncated line) must not
# raise out of on_detections and take the whole frame down with it — it is
# simply dropped, as if that one detection hadn't been seen this frame.
_REQUIRED_DETECTION_KEYS = ("class_id", "label", "confidence", "x", "y", "w", "h")


def _is_valid_detection(d) -> bool:
    return isinstance(d, dict) and all(k in d for k in _REQUIRED_DETECTION_KEYS)


# Demo-tuned defaults for the bench portal (single operator, no FC): stickier
# tracking + looser gates than the strict flight profile, so a hand-held or
# briefly-occluded subject keeps its lock. The flight/SITL path does NOT use
# FollowPipeline — sitl_followme calls follow_controller.compute() with a default
# (strict) FollowConfig — so these looser gates never reach the vehicle.
_PORTAL_CFG = dict(min_confidence=0.25, min_height_frac=0.02)   # strict flight: 0.35 / 0.05
_PORTAL_TRACKER = dict(iou_threshold=0.2, max_age=45)           # strict: 0.3 / 15  (~0.5s -> ~1.5s)


class FollowPipeline:
    """Wraps Tracker.update -> select -> compute into one call per detection
    frame. `pinned_id` (set via `set_pinned`) overrides auto-selection
    regardless of class, mirroring `target_selector.select`'s contract.

    Portal follow-me tuning (bench demo, no FC): looser gates + stickier tracker
    (see _PORTAL_CFG/_PORTAL_TRACKER) and `reacquire` — when a *pinned* subject is
    lost, re-lock the best current track of the same class (there is no re-ID, so
    it returns with a new id). This relaxes select()'s strict hold-on-pinned-loss
    for the single-operator demo; select() itself stays strict for flight. Set
    reacquire=False for strict hold (never auto-switch subject)."""

    def __init__(self, follow_class: str = "person", cfg: FollowConfig = None,
                 tracker: Tracker = None, reacquire: bool = True,
                 reacquire_after: int = 15):
        self.follow_class = follow_class
        self.cfg = cfg or FollowConfig(**_PORTAL_CFG)
        self.tracker = tracker or Tracker(**_PORTAL_TRACKER)
        self.pinned_id = None
        self.reacquire = reacquire
        # Grace period (frames the pinned subject may be missing before reacquire
        # fires). Bridges brief occlusions so the subject resumes on its ORIGINAL
        # id (via the tracker's max_age re-association) instead of snapping to a
        # same-class bystander. ~0.5 s at 30 fps.
        self.reacquire_after = reacquire_after
        self._pinned_class = None    # class_id of the pinned target while it was visible
        self._miss_count = 0         # consecutive frames the pinned target has been missing
        # Gesture-driven HOLD (Task 4): when True, on_detections still tracks
        # normally but the command is forced to a zero-velocity HOLD, same as
        # a lost/low-confidence target. Set/cleared by GestureReactor on a
        # STOP/FOLLOW ME gesture onset; also cleared by set_pinned (spec §3:
        # a video click clears a gesture STOP).
        self.paused = False

    def on_detections(self, detset_dict: dict) -> dict:
        """detset_dict: a DetectionSet-shaped dict, {"detections": [...],
        "frame": {"width":, "height":, ...}}. Both keys are read
        defensively — the wire format is a subprocess JSONL hop away, and the
        controller works in normalized [0,1] coords regardless of frame size,
        so a missing/short frame dict just falls back to (1, 1)."""
        raw_detections = detset_dict.get("detections") or []
        detections = [d for d in raw_detections if _is_valid_detection(d)]
        frame = detset_dict.get("frame") or {}
        frame_wh = (frame.get("width") or 1, frame.get("height") or 1)

        tracks = self.tracker.update(detections, frame_wh=frame_wh)
        target = select(tracks, follow_class=self.follow_class, pinned_id=self.pinned_id)

        if target is not None and target.id == self.pinned_id:
            # pinned target visible: remember its class, reset the miss counter
            self._pinned_class = target.class_id
            self._miss_count = 0
        elif self.pinned_id is not None and target is None:
            # pinned target not visible this frame
            self._miss_count += 1
            # follow-me: only after a grace period (so a briefly-occluded subject
            # can return with its original id first) re-lock the best current track
            # of the SAME class (new id, no re-ID). Class-gated so it never jumps
            # to a different kind of object.
            if (self.reacquire and self._pinned_class is not None
                    and self._miss_count > self.reacquire_after):
                candidates = [t for t in tracks if t.class_id == self._pinned_class]
                if candidates:
                    target = max(candidates, key=lambda t: t.confidence)
                    self.pinned_id = target.id
                    self._miss_count = 0

        if self.paused:
            # Gesture STOP: hold exactly like follow_controller.compute()'s own
            # "lost target" branch (zero rates, valid=False) — tracks/target
            # selection above are untouched, only the command is gated.
            command = FollowCommand(t_ns=time.monotonic_ns(),
                                    target_id=(target.id if target else None),
                                    yaw_rate=0.0, forward_vel=0.0, vertical_vel=0.0,
                                    valid=False, reason="paused (gesture)")
        else:
            command = compute(target, frame_wh, self.cfg, t_ns=time.monotonic_ns())

        return {"tracks": [t.to_dict() for t in tracks],
                "command": command.to_dict()}

    def set_pinned(self, track_id) -> None:
        """Pin a track id to follow regardless of class/confidence. Pass
        None to release the pin and return to auto-select. Also clears a
        gesture-driven `paused` HOLD (spec §3: a video click, which pins,
        clears a STOP gesture)."""
        self.pinned_id = track_id
        self._miss_count = 0
        self.paused = False
        if track_id is None:
            self._pinned_class = None
