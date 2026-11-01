"""Pure builders for garuda-mono's wire state (v2). No hailo/cv2 imports — unit-tested on x86 CI.

The snapshot is the single JSON object served by GET /api/state and streamed by
GET /api/events; services/garuda-portal/mono_client.py consumes exactly these keys.
v2 adds optional per-track `kpts` (pose keypoints) and a top-level `gesture` field.
"""
import math


def positive_range(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and 0 < value <= 30


def range_valid(raw, est, engine, age, coasting=False):
    raw_ok = isinstance(raw, (int, float)) and math.isfinite(raw) and raw > 0
    if engine != "da":
        raw_ok = raw_ok and positive_range(raw)
    return bool(raw_ok and age is not None and 0 <= age < 1500 and not coasting
                and (positive_range(est) if est is not None else engine != "da"))


def select_track(alive, lid, wanted, pinned, reacq):
    locked = next((t for t in alive if t.track_id == lid), None)
    if pinned:
        return locked  # Never substitute another person for an explicit selection.
    if locked is None and wanted:
        candidates = [t for t in alive if t.cls == wanted]
        return max(candidates, key=lambda t: (t.time_since_update == 0, t.conf), default=None)
    if locked is not None and locked.time_since_update > reacq and wanted:
        live = [t for t in alive if t.cls == wanted and t.time_since_update == 0]
        return max(live, key=lambda t: t.conf, default=locked)
    return locked


def build_snapshot(state: dict, extras: dict) -> dict:
    raw = state.get("raw")
    est = extras.get("est_m")
    valid = bool(extras.get("valid", False)) and range_valid(
        raw, est, extras.get("depth_engine"), extras.get("age_ms"), extras.get("lock_coasting", False))
    # An invalid fitted value must not silently fall back to an uncalibrated value.
    if est is not None and not positive_range(est):
        valid = False
    metric_raw = raw if extras.get("depth_engine") != "da" else None
    tracks = []
    for t in state.get("tracks_full", []):
        tid, label, x, y, w, h = t[:6]
        conf = float(t[6]) if len(t) > 6 else 0.0
        coasting = bool(t[7]) if len(t) > 7 else False
        tracks.append({"id": int(tid), "label": label, "conf": conf,
                       "x": float(x), "y": float(y), "w": float(w), "h": float(h),
                       "coasting": coasting})
        if len(t) > 8 and t[8] is not None:
            tracks[-1]["kpts"] = t[8]
        appearance = state.get("appearances", {}).get(tid)
        if appearance is not None:
            tracks[-1]["appearance"] = appearance
        if tid in state.get("faces", {}):
            tracks[-1]["face"] = state["faces"][tid]
        if tid in state.get("track_gestures", {}):
            tracks[-1]["gesture"] = state["track_gestures"][tid]
    return {
        "v": 2,
        "age_ms": extras.get("age_ms"),
        "tracks": tracks,
        "lock": {"id": extras.get("lock_id"), "class": state.get("class", ""),
                 "coasting": bool(extras.get("lock_coasting", False))},
        "distance": {"raw_m": metric_raw if valid else None, "est_m": est if valid else None,
                     "valid": valid and (est is not None or metric_raw is not None)},
        "depth_engine": extras.get("depth_engine", ""),
        "detector": {"mode": extras.get("detector_mode", "primary"),
                     "prompt": list(extras.get("prompt", []))},
        "cal": {"n": int(extras.get("cal_n", 0)), "fit": extras.get("fit")},
        "frame": {"width": int(state.get("frame_w", extras.get("frame_w", 0))),
                  "height": int(state.get("frame_h", extras.get("frame_h", 0))),
                  **({"seq": state["frame_seq"], "source": state.get("frame_source"),
                      "epoch": state.get("frame_epoch")} if "frame_seq" in state else {})},
        "gesture": extras.get("gesture") or {"name": None, "track_id": None, "held_ms": 0},
        "stats": extras.get("stats") or {},
        "face_service": extras.get("face_service") or {},
    }


def _iou_xywh(ax, ay, aw, ah, bx, by, bw, bh):
    """IoU of two (x, y, w, h) boxes in the same pixel convention."""
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def attach_kpts(tracks_full, dets):
    """Match pose-mode dets (duck-typed `.x .y .w .h .kpts`) onto tracker output by IoU.

    tracks_full: list of 8-tuples (tid, label, x, y, w, h, conf, coasting) — the tracker's own
    output, unchanged. dets: this frame's raw detections (HailoYoloPose's Dets carry `.kpts`;
    non-pose detectors' Detections don't have the attribute at all, so getattr guards them out).
    Best IoU > 0.5 wins per track; ties/no-match leave the track as the original 8-tuple (kpts
    absent -> the v2 builder above simply doesn't add the "kpts" key)."""
    kpt_dets = [d for d in dets if getattr(d, "kpts", None) is not None]
    out = []
    for t in tracks_full:
        tid, label, x, y, w, h = t[:6]
        best_iou, best_kpts = 0.5, None
        for d in kpt_dets:
            i = _iou_xywh(x, y, w, h, d.x, d.y, d.w, d.h)
            if i > best_iou:
                best_iou, best_kpts = i, d.kpts
        out.append(t + (best_kpts,) if best_kpts is not None else t)
    return out
