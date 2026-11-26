"""Consumer side of the OAK `/pair` contract, version 1 (docs/oak-coordination.md section 4).

Pure parsing and validation; no sockets, no device. `read_pair` takes anything with `.read(n)`.
`encode_pair` builds a valid envelope for tests and fixtures; the owner process has its own
producer and is not required to use it.

Envelope: 4-byte unsigned big-endian JSON length, UTF-8 JSON metadata, RGB bytes, depth bytes.
Version 1 carries packed rows only: RGB stride = width*3, depth stride = width*2. Every payload
size is therefore fixed by the expected mode and is checked BEFORE any payload is read.
"""
from oak.geometry import validate_geometry

import io
import json
import struct
from dataclasses import dataclass
from typing import Optional

import numpy as np

CONTENT_TYPE = "application/vnd.garuda.oak-pair"
SCHEMA_VERSION = 1
MAX_METADATA_BYTES = 16 * 1024
MAX_NATIVE_DIM = 8192
DEVICE_CLOCK_ID = "oak-device-monotonic"
HOST_CLOCK_ID = "CLOCK_MONOTONIC"
ABSENT_REASONS = {"omitted_by_request", "pair_wait_expired", "skew_exceeded", "stereo_unavailable"}
_DEPTH_MEASURED = ("seq", "capture_device_ns", "capture_host_ns")


class PairError(ValueError):
    """The response does not satisfy the /pair contract. The frame must not be used."""


@dataclass(frozen=True)
class Pair:
    meta: dict
    rgb: bytes                          # BGR888, packed rows (width*3 bytes each)
    depth_mm: Optional[np.ndarray]      # HxW uint16 millimetres, 0 invalid; None when absent

    @property
    def key(self):
        return self.meta["epoch"], self.meta["pair_id"]


def _need(cond, msg):
    if not cond:
        raise PairError(msg)


def _exact_read(stream, n, what):
    data = stream.read(n) if n else b""
    _need(len(data) == n, f"truncated {what}: got {len(data)} of {n} bytes")
    return data


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_count(v):
    """Non-negative integer (sequence numbers, timestamps, lengths). Floats and bools fail."""
    return _is_int(v) and v >= 0


def _is_id(v):
    return isinstance(v, str) and 0 < len(v) <= 128


def validate_meta(meta, expect_size):
    """Check metadata against the contract and the expected RGB mode (width, height)."""
    _need(isinstance(meta, dict), "metadata is not a JSON object")
    _need(meta.get("schema_version") == SCHEMA_VERSION, "unsupported schema_version")
    _need(_is_id(meta.get("epoch")), "missing epoch")
    _need(_is_count(meta.get("pair_id")), "bad pair_id")

    if "geometry" in meta:
        try:
            validate_geometry(meta['geometry'], epoch=meta['epoch'],
                              calibration_id=meta.get('calibration_id'),
                              width=expect_size[0], height=expect_size[1])
        except ValueError as exc:
            raise PairError(str(exc)) from exc

    rgb, depth = meta.get("rgb"), meta.get("depth")
    _need(isinstance(rgb, dict) and isinstance(depth, dict), "missing rgb or depth block")
    w, h = expect_size
    _need((rgb.get("width"), rgb.get("height")) == (w, h), "unexpected RGB geometry")
    _need(rgb.get("format") == "BGR888", "unexpected RGB format")
    _need(_is_int(rgb.get("stride")) and rgb["stride"] == w * 3,
          "RGB stride must be width*3 (packed rows)")
    _need(_is_int(rgb.get("length")) and rgb["length"] == w * 3 * h,
          "RGB length does not match the expected mode")
    for f in _DEPTH_MEASURED:
        _need(_is_count(rgb.get(f)), f"rgb.{f} missing or not a non-negative integer")

    clock = meta.get("clock_domain")
    _need(isinstance(clock, dict) and isinstance(clock.get("device"), dict)
          and isinstance(clock.get("host"), dict), "missing clock_domain")
    _need(clock["host"].get("id") == HOST_CLOCK_ID, "unexpected host clock")
    _need(_is_id(clock["host"].get("boot_id")), "missing host boot_id")
    _need(clock["device"].get("id") == DEVICE_CLOCK_ID, "unexpected device clock")
    _need(clock["device"].get("epoch") == meta["epoch"], "device clock epoch differs from epoch")

    _need(_is_id(meta.get("depth_reference_frame")), "missing depth_reference_frame")
    _need(_is_count(meta.get("pair_skew_limit_ns")), "missing pair_skew_limit_ns")
    _need(isinstance(meta.get("depth_valid"), bool), "depth_valid must be a boolean")
    _need(_is_count(depth.get("length")), "bad depth length")

    if meta["depth_valid"]:
        _need(meta.get("depth_reason") == "ok", "valid depth must have reason 'ok'")
        _need((depth.get("width"), depth.get("height")) == (w, h),
              "depth is not aligned to the RGB geometry")
        _need(depth.get("format") == "Z16LE", "unexpected depth format")
        _need(_is_int(depth.get("stride")) and depth["stride"] == w * 2,
              "depth stride must be width*2 (packed rows)")
        _need(depth["length"] == w * 2 * h, "depth length does not match the expected mode")
        for f in _DEPTH_MEASURED:
            _need(_is_count(depth.get(f)), f"depth.{f} missing or not a non-negative integer")
        for f in ("native_width", "native_height"):
            _need(_is_int(depth.get(f)) and 0 < depth[f] <= MAX_NATIVE_DIM, f"bad depth.{f}")
        _need(_is_id(meta.get("calibration_id")), "missing calibration_id")
        skew = meta.get("pair_skew_ns")
        _need(_is_int(skew), "pair_skew_ns missing for valid depth")
        _need(skew == depth["capture_device_ns"] - rgb["capture_device_ns"],
              "pair_skew_ns must equal depth minus RGB device capture time")
        _need(abs(skew) <= meta["pair_skew_limit_ns"], "pair skew exceeds the declared limit")
    else:
        _need(meta.get("depth_reason") in ABSENT_REASONS, "absent depth needs a known reason")
        _need(depth["length"] == 0, "absent depth must have zero length")
        for f in _DEPTH_MEASURED:
            _need(depth.get(f) is None, f"depth.{f} must be null when depth is absent")
        _need(meta.get("pair_skew_ns") is None, "pair_skew_ns must be null when depth is absent")
    return meta


def read_pair(stream, content_length, expect_size=(1280, 720), *, allow_unqualified_preview=False):
    """Read and validate one /pair response body. Raises PairError on any contract breach."""
    (n,) = struct.unpack(">I", _exact_read(stream, 4, "length prefix"))
    _need(0 < n <= MAX_METADATA_BYTES, f"metadata length {n} outside 1..{MAX_METADATA_BYTES}")
    try:
        meta = json.loads(_exact_read(stream, n, "metadata").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PairError(f"metadata is not valid UTF-8 JSON: {exc}") from None
    validate_meta(meta, expect_size)
    _need(allow_unqualified_preview or not (meta.get("measurement_qualified") is False and meta["depth_valid"]), "unqualified preview depth cannot be used for measurement")

    rgb_len, depth_len = meta["rgb"]["length"], meta["depth"]["length"]
    _need(content_length == 4 + n + rgb_len + depth_len,
          "Content-Length does not equal envelope plus declared payloads")
    rgb = _exact_read(stream, rgb_len, "RGB payload")
    depth_mm = None
    if meta["depth_valid"]:
        d = meta["depth"]
        raw = _exact_read(stream, depth_len, "depth payload")
        depth_mm = np.frombuffer(raw, dtype="<u2").reshape(d["height"], d["width"]) \
            .astype(np.uint16, copy=False)
    _need(stream.read(1) == b"", "trailing bytes after declared payloads")
    return Pair(meta, rgb, depth_mm)


class PairCursor:
    """Tracks (epoch, pair_id) so the consumer never reuses a pair or mixes two runs."""

    def __init__(self):
        self.epoch = None
        self.pair_id = None

    def query(self, want_depth=True):
        q = f"depth={1 if want_depth else 0}"
        if self.epoch is not None:
            q = f"after={self.pair_id}&epoch={self.epoch}&" + q
        return "/pair?" + q

    def accept(self, pair):
        """Returns 'first', 'next' or 'epoch_changed'. Raises PairError on a repeated or
        backwards pair_id within one epoch. On 'epoch_changed' the caller must discard all
        pairing and tracking state from the previous run."""
        epoch, pair_id = pair.key
        if self.epoch is None:
            outcome = "first"
        elif epoch != self.epoch:
            outcome = "epoch_changed"
        else:
            _need(pair_id > self.pair_id, "pair_id did not increase within the epoch")
            outcome = "next"
        self.epoch, self.pair_id = epoch, pair_id
        return outcome


def encode_pair(rgb, depth_mm=None, *, pair_id=0, epoch="test-epoch", rgb_seq=0, depth_seq=0,
                rgb_device_ns=1_000_000_000, depth_device_ns=1_000_000_000,
                host_offset_ns=5_000_000_000, skew_limit_ns=33_333_333,
                reason="pair_wait_expired", size=(1280, 720), reference_frame="unverified",
                boot_id="test-boot", calibration_id="test-cal", native=(640, 400)):
    """Build a contract-valid envelope. Returns (body_bytes, meta). For tests and fixtures."""
    w, h = size
    valid = depth_mm is not None
    depth_bytes = np.ascontiguousarray(depth_mm, dtype="<u2").tobytes() if valid else b""
    meta = {
        "schema_version": SCHEMA_VERSION, "epoch": epoch, "pair_id": pair_id,
        "rgb": {"seq": rgb_seq, "capture_device_ns": rgb_device_ns,
                "capture_host_ns": rgb_device_ns + host_offset_ns,
                "width": w, "height": h, "stride": w * 3, "format": "BGR888", "length": len(rgb)},
        "depth": {"seq": depth_seq if valid else None,
                  "capture_device_ns": depth_device_ns if valid else None,
                  "capture_host_ns": depth_device_ns + host_offset_ns if valid else None,
                  "width": w, "height": h, "stride": w * 2, "format": "Z16LE",
                  "length": len(depth_bytes), "native_width": native[0], "native_height": native[1]},
        "depth_reference_frame": reference_frame, "calibration_id": calibration_id,
        "depth_valid": valid, "depth_reason": "ok" if valid else reason,
        "pair_skew_ns": depth_device_ns - rgb_device_ns if valid else None,
        "pair_skew_limit_ns": skew_limit_ns,
        "clock_domain": {"device": {"id": DEVICE_CLOCK_ID, "epoch": epoch},
                         "host": {"id": HOST_CLOCK_ID, "boot_id": boot_id}},
    }
    return pack(meta, rgb, depth_bytes), meta


def pack(meta, rgb, depth_bytes=b""):
    blob = json.dumps(meta).encode("utf-8")
    return struct.pack(">I", len(blob)) + blob + bytes(rgb) + depth_bytes


def parse_bytes(body, expect_size=(1280, 720)):
    return read_pair(io.BytesIO(body), len(body), expect_size)
