# conscio/perception/sensor.py
"""SensorAdapter — the pluggable perception surface.

The symmetric mirror of `conscio.agency.adapter.InferenceAdapter`: F1 made
*inference* and *action* pluggable; this makes *perception* pluggable too. A
sensor produces a `PerceptionFrame`; the caller turns the frame into the
`world_state` string that `engine.reflect()` already accepts. `reflect()` is
therefore never touched — `PerceptionFrame.to_world_state()` is the only seam,
and it is a pure, deterministic string builder.

The interface is frozen here (v1.3) so the v1.5 "Live" daemon consumes it without
redesign. v1.3 ships the interface, `MockSensor`, and reference *examples*; the
production reference sensors (HostSensor / AgentSensor) and the daemon that
*calls* a sensor each cycle are F5.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from ..risk import Risk


@dataclass
class PerceptionFrame:
    """One observation snapshot from a sensor.

    `to_world_state()` assembles the frame into the plain-string `world_state`
    that `engine.reflect()` consumes — deterministically (no clock, no rng), so
    the same frame always yields the same string. `ts` is deliberately excluded
    from that string so determinism holds regardless of when it was stamped.

    `ts` is **epoch seconds** (`time.time()`), matching the `ActionLedger`'s
    `ts REAL` convention; `0.0` means unset. The caller stamps it.
    """

    source: str
    observations: list[str]
    signals: dict[str, float] = field(default_factory=dict)
    ts: float = 0.0          # epoch seconds (time.time()); 0.0 = unset

    def to_world_state(self) -> str:
        lines = [f"[{self.source}]"]
        lines.extend(self.observations)
        for key in sorted(self.signals):
            lines.append(f"{key}={self.signals[key]}")
        return "\n".join(lines)


class SensorAdapter(ABC):
    """Abstract perception backend. Subclass and implement `perceive()`.

    `risk` classifies the sensor in the shared safety vocabulary: read-only
    sensors are LOW; sensors that probe external services rate higher.
    """

    name: str = "sensor"
    risk: Risk = Risk.LOW

    @abstractmethod
    def perceive(self) -> PerceptionFrame:
        """Return the current perception snapshot."""
        raise NotImplementedError


class MockSensor(SensorAdapter):
    """Deterministic test double — the perception mirror of `MockAdapter`.

    Replays a fixed sequence of frames; raises `StopIteration` once exhausted.
    """

    name = "mock"

    def __init__(self, frames: Sequence[PerceptionFrame]) -> None:
        self.frames: list[PerceptionFrame] = list(frames)
        self._it: Iterator[PerceptionFrame] = iter(self.frames)

    def perceive(self) -> PerceptionFrame:
        return next(self._it)
