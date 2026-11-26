"""ICaptureBackend — the camera plugin contract (CLAUDE.md §1).

Implementations: hosts/pi5 libcamera backend (bench); Hailo-15 Media Library (product).
No implementation-specific types cross this boundary.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from .messages import FrameMeta

FrameCallback = Callable[[FrameMeta, memoryview], None]


class ICaptureBackend(ABC):
    @abstractmethod
    def configure(self, width: int, height: int, fps: float,
                  pixel_format: str = "RGB888", trigger_external: bool = False) -> None:
        """Configure the stream. trigger_external requires a 1.8V-safe trigger path
        (IMX296 XTR — see CLAUDE.md hardware safety rule)."""

    @abstractmethod
    def start(self, on_frame: FrameCallback) -> None:
        """Begin streaming; on_frame is called per frame with metadata + pixel view."""

    @abstractmethod
    def stop(self) -> None:
        """Stop streaming; safe to call repeatedly."""
