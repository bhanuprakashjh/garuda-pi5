"""Stubbed topic layer (pre-Phase-4 per CLAUDE.md §3).

One Publisher protocol; today's implementation is JSONL to a stream (stdout or a
file/socket). At Phase 4 a DDS/ROS 2 publisher implements the same protocol and
services swap it in via constructor injection — no other code changes.
"""
from __future__ import annotations

import json
import sys
from typing import Protocol, TextIO


class Publisher(Protocol):
    def publish(self, topic: str, message: dict) -> None: ...


class JsonlPublisher:
    def __init__(self, stream: TextIO = sys.stdout):
        self._stream = stream

    def publish(self, topic: str, message: dict) -> None:
        self._stream.write(json.dumps({"topic": topic, "msg": message},
                                      separators=(",", ":")) + "\n")
        self._stream.flush()
