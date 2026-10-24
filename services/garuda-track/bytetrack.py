# services/garuda-track/bytetrack.py  — lean ByteTrack: linear motion + two-stage greedy IoU (no scipy)
from __future__ import annotations


def _iou(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


class Track:
    _next = 1

    def __init__(self, bbox, cls, conf, min_hits):
        self.track_id = Track._next; Track._next += 1
        self.bbox = list(map(float, bbox)); self.cls = cls; self.conf = conf
        self.vel = [0.0, 0.0]                 # velocity of top-left
        self.last_x, self.last_y = float(bbox[0]), float(bbox[1])  # position at last update (not predicted)
        self.age = 1; self.hits = 1; self.time_since_update = 0; self._min_hits = min_hits

    @property
    def confirmed(self):
        return self.hits >= self._min_hits

    def predict(self):
        self.bbox[0] += self.vel[0]; self.bbox[1] += self.vel[1]
        self.age += 1; self.time_since_update += 1

    def update(self, bbox, cls, conf):
        nx, ny = float(bbox[0]), float(bbox[1])
        self.vel = [nx - self.last_x, ny - self.last_y]   # displacement since last update
        self.last_x, self.last_y = nx, ny
        self.bbox = [nx, ny, float(bbox[2]), float(bbox[3])]
        self.cls = cls; self.conf = conf; self.hits += 1; self.time_since_update = 0


def _greedy_match(tracks, dets, iou_thr):
    """Return (matches[(ti,di)], unmatched_tracks, unmatched_dets) by descending IoU, greedy."""
    pairs = []
    for ti, t in enumerate(tracks):
        for di, d in enumerate(dets):
            v = _iou(t.bbox, d[0])
            if v >= iou_thr:
                pairs.append((v, ti, di))
    pairs.sort(reverse=True)
    ut, ud = set(range(len(tracks))), set(range(len(dets)))
    matches = []
    for _, ti, di in pairs:
        if ti in ut and di in ud:
            matches.append((ti, di)); ut.discard(ti); ud.discard(di)
    return matches, ut, ud


class ByteTracker:
    def __init__(self, high_thr=0.5, low_thr=0.1, iou_thr=0.3, max_lost=30, min_hits=3):
        self.high_thr, self.low_thr, self.iou_thr = high_thr, low_thr, iou_thr
        self.max_lost, self.min_hits = max_lost, min_hits
        self.tracks: list[Track] = []

    def update(self, dets):
        for t in self.tracks:
            t.predict()
        high = [d for d in dets if d[2] >= self.high_thr]
        low = [d for d in dets if self.low_thr <= d[2] < self.high_thr]
        # stage 1: high-score dets vs all tracks
        m1, ut, ud = _greedy_match(self.tracks, high, self.iou_thr)
        for ti, di in m1:
            self.tracks[ti].update(high[di][0], high[di][1], high[di][2])
        # stage 2: low-score dets vs still-unmatched tracks (ByteTrack's key idea)
        rem_tracks = [self.tracks[i] for i in ut]
        m2, _, _ = _greedy_match(rem_tracks, low, self.iou_thr)
        for ti, di in m2:
            rem_tracks[ti].update(low[di][0], low[di][1], low[di][2])
        # births: unmatched high-score dets -> new tracks
        for di in ud:
            b, c, s = high[di]
            self.tracks.append(Track(b, c, s, self.min_hits))
        # cull tracks lost too long
        self.tracks = [t for t in self.tracks if t.time_since_update <= self.max_lost]
        return [t for t in self.tracks if t.time_since_update == 0 and t.confirmed]
