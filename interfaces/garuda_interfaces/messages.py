"""Garuda message schemas — transport-neutral dataclasses (see interfaces/README.md).

Serialization: `to_dict()` -> plain JSON-able dict; the stub topic layer emits JSONL.
At Phase 4 these map 1:1 onto DDS/ROS 2 types. Timestamps: monotonic ns unless _ptp_ns.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import List, Optional

SCHEMA_VERSION = "0.1.0"


@dataclass
class FrameMeta:
    frame_id: int
    t_capture_ns: int              # monotonic capture timestamp
    clock_domain: str              # "mono" | "ptp"
    sensor: str                    # e.g. "imx708.cam0"
    width: int
    height: int
    pixel_format: str              # e.g. "RGB888"
    exposure_us: Optional[int] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class Detection:
    class_id: int
    label: str
    confidence: float              # 0..1
    # bbox normalized to [0,1] in the *source frame* geometry of FrameMeta
    x: float
    y: float
    w: float
    h: float

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class DetectionSet:
    frame: FrameMeta
    detections: List[Detection]
    t_infer_done_ns: int
    model: str                     # e.g. "yolov8m_h10@5.1.1"
    schema: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "frame": self.frame.to_dict(),
            "detections": [d.to_dict() for d in self.detections],
            "t_infer_done_ns": self.t_infer_done_ns,
            "model": self.model,
        }


@dataclass
class OdometrySample:
    """Pose output with mandatory self-assessed health (P5 failsafe contract).

    A producer MUST inflate covariance and/or clear `valid` when its own tracking
    degrades or input goes stale. The FC provably cannot detect a frozen feed
    (bench/benchlog.md 2026-07-30) — health lives here, at the producer.
    """
    t_sample_ns: int
    position_m: List[float]                    # [x, y, z] NED
    orientation_q: List[float]                 # [w, x, y, z]
    velocity_mps: Optional[List[float]] = None
    pose_covariance: Optional[List[float]] = None   # 6x6 row-major, pos+rot
    valid: bool = True
    tracking_quality: float = 1.0              # 0..1, producer-defined
    t_source_data_ns: int = 0                  # newest sensor data contributing
    schema: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class Track:
    id: int
    class_id: int
    label: str
    bbox: tuple                  # normalized (x, y, w, h), top-left origin
    confidence: float
    hits: int = 1
    age: int = 1
    time_since_update: int = 0
    schema: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {"id": self.id, "class_id": self.class_id, "label": self.label,
                "bbox": list(self.bbox), "confidence": self.confidence,
                "hits": self.hits, "age": self.age,
                "time_since_update": self.time_since_update,
                "schema": self.schema}


@dataclass
class FollowCommand:
    t_ns: int
    target_id: object            # int or None
    yaw_rate: float              # rad/s, +right
    forward_vel: float           # m/s, +toward target
    vertical_vel: float          # m/s, +up
    valid: bool
    reason: str
    schema: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {"t_ns": self.t_ns, "target_id": self.target_id,
                "yaw_rate": self.yaw_rate, "forward_vel": self.forward_vel,
                "vertical_vel": self.vertical_vel, "valid": self.valid,
                "reason": self.reason, "schema": self.schema}
