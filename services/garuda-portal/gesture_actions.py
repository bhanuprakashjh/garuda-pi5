"""GestureReactor — turns mono's `gesture` field (snapshot v2, Task 3) into
portal-side actions: STOP pauses FollowPipeline, FOLLOW ME unpauses + re-locks
onto the gesturing subject via mono's own click-to-lock endpoint. POINT_* is
HUD-only (no action) for this task; a later task may wire it to yaw nudges.

Fires once per gesture ONSET, not once per frame — mono re-publishes the same
active gesture on every tick while held. STOP/POINT_* onset is a change in
`gesture.name` alone. FOLLOW ME onset is keyed on `(name, track_id)`: mono's
handoff arbitration (bystander takeover, largest-bystander reselection) can
change `track_id` while `name` stays "follow_me" with no name transition, and
that id change must still re-fire the re-lock (a re-POST at the new subject's
center is harmless).

Contract hazard (Task 3, carried forward): `gesture.name` can be non-null
while `track_id` is null for up to ~release_ms (subject-FSM release-hysteresis
window). For FOLLOW ME, a null/missing track_id or a track_id absent from this
snapshot's `tracks[]` must be a no-op — no pipeline mutation, no POST, no crash.
Such a skipped attempt must NOT consume the onset: the follow_id dedup state
only commits when the action actually fires, so the action still fires once a
valid, resolvable track_id shows up later on the same hold.
"""
from __future__ import annotations

import threading
import urllib.request


def _http_post(url: str, timeout: float = 2.0) -> None:
    urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=timeout)


class GestureReactor:
    def __init__(self, bus, pipeline, mono_base: str, post=_http_post):
        self.bus = bus
        self.pipeline = pipeline
        self.mono_base = mono_base.rstrip("/")
        self.post = post
        self._last_name = None
        self._last_follow_track_id = None   # committed only when follow_me actually fires
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        q = self.bus.subscribe()
        while True:
            ev = q.get()
            if ev.get("kind") != "mono":
                continue
            try:
                self._handle(ev.get("data") or {})
            except Exception:
                pass   # a single malformed/unexpected snapshot must not kill the thread

    def _handle(self, snap: dict) -> None:
        gesture = snap.get("gesture")
        name = gesture.get("name") if isinstance(gesture, dict) else None

        if name == "follow_me":
            track_id = gesture.get("track_id") if isinstance(gesture, dict) else None
            if name == self._last_name and track_id == self._last_follow_track_id:
                return                  # not an onset: still the same subject, same hold
            self._last_name = name
            self._on_follow_me(snap, gesture)
            return

        if name == self._last_name:
            return                      # not an onset (same as last tick, incl. held gestures)
        self._last_name = name
        self._last_follow_track_id = None   # left follow_me (or never entered) — reset its dedup

        if name == "stop":
            self.pipeline.paused = True
        # point_left / point_right / None / unknown -> HUD-only, no action

    def _on_follow_me(self, snap: dict, gesture: dict) -> None:
        track_id = gesture.get("track_id")
        if track_id is None:
            return                      # skip; onset NOT consumed — _last_follow_track_id untouched
        track = next((t for t in snap.get("tracks") or [] if t.get("id") == track_id), None)
        if track is None:
            return                      # skip; onset NOT consumed — _last_follow_track_id untouched
        self._last_follow_track_id = track_id   # commit only on an actual fire
        self.pipeline.paused = False
        self.pipeline.set_pinned(None)
        cx = round(float(track.get("x", 0.0)) + float(track.get("w", 0.0)) / 2.0)
        cy = round(float(track.get("y", 0.0)) + float(track.get("h", 0.0)) / 2.0)
        self.post(f"{self.mono_base}/api/mono_click?x={cx}&y={cy}")
