"""Pure builders for garuda-mono's wire state (v2). No hailo/cv2 imports — unit-tested on x86 CI.

The snapshot is the single JSON object served by GET /api/state and streamed by
GET /api/events; services/garuda-portal/mono_client.py consumes exactly these keys.
v2 adds optional per-track `kpts` (pose keypoints) and a top-level `gesture` field.
"""


def build_snapshot(state: dict, extras: dict) -> dict:
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
    return {
        "v": 2,
        "age_ms": extras.get("age_ms"),
        "tracks": tracks,
        "lock": {"id": extras.get("lock_id"), "class": state.get("class", ""),
                 "coasting": bool(extras.get("lock_coasting", False))},
        "distance": {"raw_m": state.get("raw"), "est_m": extras.get("est_m"),
                     "valid": bool(extras.get("valid", False))},
        "depth_engine": extras.get("depth_engine", ""),
        "detector": {"mode": extras.get("detector_mode", "primary"),
                     "prompt": list(extras.get("prompt", []))},
        "cal": {"n": int(extras.get("cal_n", 0)), "fit": extras.get("fit")},
        "frame": {"width": int(extras.get("frame_w", 0)), "height": int(extras.get("frame_h", 0))},
        "gesture": extras.get("gesture") or {"name": None, "track_id": None, "held_ms": 0},
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
