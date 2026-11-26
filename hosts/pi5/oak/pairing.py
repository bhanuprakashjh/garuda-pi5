"""Bounded device-timestamp pairing and /pair v1 serialization. No SDK dependency."""
import json
import struct
import time
import uuid
import zlib
from collections import deque
from dataclasses import dataclass

DEPTH_ENCODINGS = ('raw', 'zlib1')

def pack_depth_preview(meta, payload, encoding):
    """Serialize a depth-only preview. zlib level 1 is lossless; zero (no result) stays zero."""
    if encoding not in DEPTH_ENCODINGS:
        raise ValueError('unsupported depth encoding')
    body = zlib.compress(payload, 1) if encoding == 'zlib1' else payload
    m = dict(meta, depth_encoding=encoding, depth_encoded_length=len(body))
    blob = json.dumps(m, separators=(',', ':')).encode()
    assert len(blob) <= 16384
    return struct.pack('>I', len(blob))+blob+body

LIMITS = dict(pair_wait_ms=2000, depth_wait_ms=80, stale_age_ms=1000,
              rgb_queue=4, depth_queue=4, pair_skew_limit_ns=33_333_333)


@dataclass(frozen=True)
class Frame:
    seq: int
    device_ns: int
    host_ns: int
    payload: bytes
    arrival_ns: int


class Pairing:
    """Single capture-thread writer; callers protect access using the owner condition."""
    def __init__(self, boot_id, calibration_id, size=(1280, 720), limits=None):
        self.limits = dict(LIMITS if limits is None else limits)
        self.boot_id, self.calibration_id, self.size = boot_id, calibration_id, size
        self.epoch = uuid.uuid4().hex
        self.rgb, self.depth = deque(), deque()
        self.latest = None
        self.geometry = None
        self.depth_backend = "oak-rvc2"
        self.measurement_qualified = True
        self.qualification_reason = None
        self.publish_host_ns = None
        self.next_id = 0
        self.stats = dict(rgb_overwrites=0, depth_overwrites=0, paired=0, missing=0)

    def add(self, kind, frame):
        queue = getattr(self, kind)
        if len(queue) >= self.limits[kind + '_queue']:
            queue.popleft()
            self.stats[kind + '_overwrites'] += 1
        queue.append(frame)

    def advance(self, now_ns=None):
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        while self.rgb:
            rgb = self.rgb[0]
            nearest = min(self.depth, key=lambda d: abs(d.device_ns-rgb.device_ns), default=None)
            valid = nearest is not None and abs(nearest.device_ns-rgb.device_ns) <= self.limits['pair_skew_limit_ns']
            if not valid and now_ns-rgb.arrival_ns < self.limits['depth_wait_ms']*1_000_000:
                break
            newer_depth = any(d.device_ns >= rgb.device_ns for d in self.depth)
            reason = 'ok' if valid else ('skew_exceeded' if newer_depth else 'pair_wait_expired')
            self.latest = (self.next_id, rgb, nearest if valid else None, reason)
            self.next_id += 1
            self.stats['paired' if valid else 'missing'] += 1
            self.rgb.popleft()
            # Consumed and older depths cannot be reused for a later RGB exposure.
            if valid:
                while self.depth and self.depth[0].device_ns <= nearest.device_ns:
                    self.depth.popleft()

    def publish_rgb(self, frame):
        """Independent unqualified RGB stream; never waits for stereo completion."""
        if self.measurement_qualified:
            raise ValueError('RGB-only publication requires explicit unqualified mode')
        self.latest=(self.next_id,frame,None,'stereo_unavailable')
        self.next_id+=1

    def response(self, after=None, epoch=None, want_depth=True, now_ns=None, preview=False,
                 omit_rgb=False):
        """omit_rgb serves only the display-only /depth-preview route and returns
        (200, (metadata, depth_payload)) so the caller can encode outside the owner lock.
        That envelope deliberately fails the /pair contract (RGB length 0), so a
        measurement consumer cannot mistake it for a pair."""
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        if self.latest is None:
            return 503, None
        pid, rgb, depth, reason = self.latest
        if not 0 <= now_ns-rgb.host_ns < self.limits['stale_age_ms']*1_000_000:
            return 503, None
        if after is not None and epoch == self.epoch and pid <= after:
            return 204, None
        if not self.measurement_qualified and not preview:
            depth, reason = None, 'stereo_unavailable'
        if not want_depth:
            depth, reason = None, 'omitted_by_request'
        if omit_rgb and (not preview or self.measurement_qualified or depth is None):
            return 503, None
        w, h = self.size
        def meta(frame, channels, fmt):
            return dict(seq=frame.seq if frame else None,
                        capture_device_ns=frame.device_ns if frame else None,
                        capture_host_ns=frame.host_ns if frame else None,
                        width=w, height=h, stride=w*channels, format=fmt,
                        length=len(frame.payload) if frame else 0)
        m = dict(schema_version=1, epoch=self.epoch, pair_id=pid,
                 rgb=meta(rgb, 3, 'BGR888'), depth=meta(depth, 2, 'Z16LE'),
                 depth_reference_frame='unverified', calibration_id=self.calibration_id,
                 depth_valid=depth is not None, depth_reason=reason,
                 pair_skew_ns=depth.device_ns-rgb.device_ns if depth else None,
                 pair_skew_limit_ns=self.limits['pair_skew_limit_ns'],
                 clock_domain=dict(device=dict(id='oak-device-monotonic', epoch=self.epoch),
                                   host=dict(id='CLOCK_MONOTONIC', boot_id=self.boot_id)))
        m['depth_backend'] = self.depth_backend
        m['measurement_qualified'] = self.measurement_qualified
        m['qualification_reason'] = self.qualification_reason
        m['publish_host_ns'] = self.publish_host_ns
        m['preview_only'] = not self.measurement_qualified and preview
        m['depth'].update(native_width=640, native_height=400)
        if self.geometry is not None:
            m['geometry'] = dict(self.geometry)
        if omit_rgb:
            m['rgb']['length'] = 0
            m['rgb_omitted'] = True
            return 200, (m, depth.payload)
        blob = json.dumps(m, separators=(',', ':')).encode()
        assert len(blob) <= 16384
        return 200, struct.pack('>I', len(blob))+blob+rgb.payload+(depth.payload if depth else b'')
