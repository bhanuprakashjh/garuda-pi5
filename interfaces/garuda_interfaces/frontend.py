"""IFrontEnd — VIO front-end contract (garuda-vio wraps Basalt behind this)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence, Tuple

from .messages import FrameMeta, OdometrySample


class IFrontEnd(ABC):
    @abstractmethod
    def feed_stereo(self, left: Tuple[FrameMeta, memoryview],
                    right: Tuple[FrameMeta, memoryview]) -> None:
        """Feed a hardware-synchronized stereo pair."""

    @abstractmethod
    def feed_imu(self, t_ns: int, gyro_rps: Sequence[float],
                 accel_mps2: Sequence[float]) -> None:
        """Feed one IMU sample (same clock domain as frames)."""

    @abstractmethod
    def latest_pose(self) -> Optional[OdometrySample]:
        """Most recent pose WITH covariance/validity per the failsafe contract."""
