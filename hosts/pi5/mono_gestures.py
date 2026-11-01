"""Pure gesture rules on COCO-17 keypoints (pixel coords). No hailo/cv2 imports — x86 CI-tested.

Constants are bench-tuned once (plan Task 5) then frozen here + in models/yolov8m_pose_h10.md.
Image y grows down: "above" = smaller y.
"""
import time

NOSE, LSHO, RSHO, LWRI, RWRI = 0, 5, 6, 9, 10
CONF_FLOOR = 0.35
V_ALIGN_FRAC = 0.18    # |wrist.x - shoulder.x| <= frac*box_w  -> arm counts as vertical
H_BAND_FRAC = 0.12     # |wrist.y - shoulder.y| <= frac*box_h  -> wrist at shoulder height
H_EXT_FRAC = 0.30      # |wrist.x - shoulder.x| >= frac*box_w  -> arm counts as extended
                       # (plan said 0.35; 0.30 is deliberate — 0.35 fails the plan's own
                       # point_left/right tests: 60 px extension on a 200 px box. 2026-08-10)


def classify_pose(kpts, box_w, box_h, conf_floor=CONF_FLOOR):
    """Per-rule confidence gating (owner-ruled, bench tuning 2026-08-10): each gesture requires
    only the keypoints it actually uses; an unconfident keypoint reads as "not raised / not
    extended", never a global abort. Rescues one-arm gestures at range, where the idle wrist
    (occluded near the hip) and the nose routinely drop below the floor while the gesturing
    arm stays confident. Documented edge: a both-arms STOP with one unconfident wrist reads as
    the confident arm alone — follow_me if that raise is vertical, else None (see model card)."""
    def ok(i): return kpts[i][2] >= conf_floor
    nose_ok = ok(NOSE)
    nose_y = kpts[NOSE][1]

    def above_nose(wri):       # STOP-style raise: confidently above the nose, any alignment
        return nose_ok and ok(wri) and kpts[wri][1] < nose_y

    def up(wri, sho):          # FOLLOW-style raise: above nose AND roughly vertical
        return (above_nose(wri) and ok(sho)
                and abs(kpts[wri][0] - kpts[sho][0]) <= V_ALIGN_FRAC * box_w)

    if above_nose(LWRI) and above_nose(RWRI):
        return "stop"
    l_up, r_up = up(LWRI, LSHO), up(RWRI, RSHO)
    if l_up != r_up:
        return "follow_me"
    for wri, sho, name in ((LWRI, LSHO, "point_left"), (RWRI, RSHO, "point_right")):
        if (ok(wri) and ok(sho)
                and abs(kpts[wri][1] - kpts[sho][1]) <= H_BAND_FRAC * box_h
                and abs(kpts[wri][0] - kpts[sho][0]) >= H_EXT_FRAC * box_w):
            return name
    return None


class GestureFSM:
    """Debounce with symmetric hysteresis: emit only after hold_ms of continuous identical classification.
    Survive dropouts shorter than release_ms (15 fps flicker must not chatter or restart hold).
    Gaps of >= release_ms reset the candidate hold timer; active release resets the candidate
    only when it still belongs to the released gesture (a different gesture mid-hold — a direct
    stop->follow_me switch — keeps its accumulated hold). Reappearance after release always
    requires a fresh hold_ms."""

    def __init__(self, hold_ms=1000, release_ms=400, clock=time.monotonic):
        self.hold_ms, self.release_ms, self.clock = hold_ms, release_ms, clock
        self._cand = None; self._cand_t = 0.0
        self._cand_gap_start_t = 0.0  # When a gap from the current candidate began
        self._active = None; self._last_seen_t = 0.0

    def update(self, name):
        now = self.clock()

        # Handle candidate gap tracking: if candidate reappears after >= release_ms gap, reset its hold timer
        if self._cand is not None:
            if name == self._cand:
                # Candidate is being seen again
                if self._cand_gap_start_t > 0 and (now - self._cand_gap_start_t) * 1000 >= self.release_ms:
                    # Gap was >= release_ms, reset the hold timer for a fresh start
                    self._cand_t = now
                self._cand_gap_start_t = 0.0  # Clear gap marker
            else:
                # Candidate is not being seen (None or different gesture)
                if self._cand_gap_start_t == 0.0:
                    self._cand_gap_start_t = now  # Start tracking gap

                # If gap has exceeded release_ms, clear the candidate entirely
                if (now - self._cand_gap_start_t) * 1000 >= self.release_ms:
                    self._cand = None
                    self._cand_t = 0.0
                    self._cand_gap_start_t = 0.0

        # Process incoming gesture
        if name is not None:
            if name != self._cand:
                # New candidate gesture
                self._cand, self._cand_t = name, now
                self._cand_gap_start_t = 0.0

            # Check if candidate has held long enough to activate
            if (now - self._cand_t) * 1000 >= self.hold_ms:
                self._active = name

            # Update active gesture last-seen time (for flicker tolerance during release)
            if name == self._active:
                self._last_seen_t = now

        # Handle active gesture release: wait release_ms after last sighting
        if self._active is not None and name != self._active:
            if (now - self._last_seen_t) * 1000 >= self.release_ms:
                released = self._active
                self._active = None
                if self._cand == released:  # reset candidate on release — but never clobber a
                    self._cand = None       # DIFFERENT gesture already accumulating its hold
                    self._cand_t = 0.0
                    self._cand_gap_start_t = 0.0

        # Calculate held_ms: time since candidate was first seen (only if active)
        held = int((now - self._cand_t) * 1000) if self._active else 0
        return {"name": self._active, "held_ms": held}


def classify_demo_pose(kpts, box_w, box_h):
    """Demo-only flight gestures; leave the existing portal gesture contract unchanged."""
    if len(kpts) != 17 or box_w <= 0 or box_h <= 0:
        return None
    both = all(kpts[i][2] >= CONF_FLOOR for i in (LSHO, RSHO, LWRI, RWRI))
    if both:
        ls, rs, lw, rw = (kpts[i] for i in (LSHO, RSHO, LWRI, RWRI))
        direction = 1 if rs[0] > ls[0] else -1
        # Body-box width expands with outstretched arms. Normalize by shoulders instead.
        shoulders = abs(rs[0]-ls[0])
        left_extension = (ls[0]-lw[0])*direction
        right_extension = (rw[0]-rs[0])*direction
        if (shoulders >= 8
                and left_extension >= .65*shoulders
                and right_extension >= .65*shoulders
                and abs(lw[1]-ls[1]) <= .45*left_extension
                and abs(rw[1]-rs[1]) <= .45*right_extension):
            return 'circle'
        # Cross both wrists over the torso midline at chest height.
        mid = (ls[0]+rs[0])/2
        chest_top = min(ls[1], rs[1])
        if ((lw[0]-mid)*direction > .04*box_w
                and (mid-rw[0])*direction > .04*box_w
                and all(chest_top <= p[1] <= chest_top+.3*box_h for p in (lw, rw))):
            return 'land'
    name = classify_pose(kpts, box_w, box_h)
    # A partially hidden two-hand STOP must not become a one-arm launch command.
    if name == 'follow_me' and not both:
        return None
    return name


def track_gestures(tracks, fsms):
    """Independent gesture state per person; no bystander can operate another lock."""
    live_ids = {t[0] for t in tracks}
    for tid in list(fsms):
        if tid not in live_ids:
            del fsms[tid]
    result = {}
    for t in tracks:
        name = None
        if not t[7] and len(t)>8 and t[8] is not None:
            name=classify_demo_pose(t[8],t[4],t[5])
            # New demo uses screen directions; existing portal gesture names unchanged.
            if name in ('point_left','point_right'):
                wrist, shoulder = (LWRI,LSHO) if name=='point_left' else (RWRI,RSHO)
                name='point_left' if t[8][wrist][0]<t[8][shoulder][0] else 'point_right'
        result[t[0]]=fsms.setdefault(t[0],GestureFSM()).update(name)
    return result
