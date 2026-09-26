# conscio/ambient/connectors.py
"""Connectors wake an agent that is not live (§7.4-§7.6). A connector knows
its runtime's CLI and nothing about the board: the node claims the task,
builds the env and the prompt, and hands them over."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

SPAWN_REASONS = ("spawn_error", "timeout", "rate_limited")


@dataclass(frozen=True)
class Spawned:
    session_id: str
    pid: int | None


class SpawnFailed(Exception):
    """reason is one of SPAWN_REASONS: it becomes wake_failed (reason: <r>)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in SPAWN_REASONS:
            raise ValueError(f"unknown spawn failure {reason!r}")
        super().__init__(detail or reason)
        self.reason = reason


class Connector(Protocol):
    name: str

    def spawn(self, *, entry: dict, prompt: str, env: dict[str, str],
              cwd: str) -> Spawned: ...

    def is_active(self, session_id: str) -> bool | None: ...

    def stop(self, session_id: str) -> None: ...


CONNECTORS: dict[str, Connector] = {}    # claude-bg lands with task 8, after probes S1/S2
