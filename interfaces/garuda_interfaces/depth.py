"""IDepthSource — depth plugin contract (Arducam ToF first; stereo depth later)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from .messages import FrameMeta

DepthCallback = Callable[[FrameMeta, memoryview], None]  # depth in mm, uint16


class IDepthSource(ABC):
    @abstractmethod
    def start(self, on_depth: DepthCallback) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...
