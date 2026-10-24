"""Pick which track to follow.

If `pinned_id` is set, the matching track is returned regardless of
follow_class — but if that track is not present in `tracks` (the pinned
subject is occluded or has left frame), this returns None rather than
falling back to auto-selecting a different subject. A pinned target that is
no longer visible yields None — the caller HOLDs and waits for a re-pin; it
does not silently switch to a different subject. There is no re-ID this
increment, so a returning subject gets a new id and must be re-pinned.

If `pinned_id` is None (no pin set), the highest-confidence track of
follow_class is auto-selected, or None if there is no match.
"""
from __future__ import annotations

def select(tracks, follow_class="person", pinned_id=None):
    if pinned_id is not None:
        for t in tracks:
            if t.id == pinned_id:
                return t
        return None
    candidates = [t for t in tracks if t.label == follow_class]
    if not candidates:
        return None
    return max(candidates, key=lambda t: t.confidence)
