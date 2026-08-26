# conscio/squads/_base.py
"""Voice protocol for Conscio squads (v4.4).

Every squad voice implements the same contract:

- ``analyze(ctx) -> VoiceResult``       — deterministic, always available.
- ``analyze_llm(ctx, adapter) -> VoiceResult`` — opt-in LLM path. Default
  raises NotImplementedError; a voice that supports LLM overrides it.

A VoiceResult carries ``{role, analysis, concerns, vote}`` mirroring the
Council voice shape so downstream consumers (EventBus, MCP serialization)
treat squads uniformly.

Determinism rule: ``analyze()`` is pure (stdlib-only, no LLM). Squad voices
never make network calls in the deterministic path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import Protocol

    class _Adapter(Protocol):
        def generate(self, prompt: str, max_tokens: int = 128,
                     temperature: float = 0.3) -> Any: ...


class VoiceVote(Enum):
    """Allowed vote values for a squad voice result."""

    PROCEED = "proceed"
    HOLD = "hold"
    VETO = "veto"


@dataclass
class VoiceResult:
    """Structured output of a voice analysis.

    Mirrors the shape consumed by Council consumers so squads are
    interchangeable at the event/emission layer.
    """

    role: str
    analysis: str
    concerns: list[str] = field(default_factory=list)
    vote: str = "proceed"

    def __post_init__(self) -> None:
        if self.vote not in (v.value for v in VoiceVote):
            raise ValueError(
                f"Invalid vote '{self.vote}'. Must be one of: "
                f"{[v.value for v in VoiceVote]}"
            )


def _vote_from_concerns(concerns: list[str]) -> str:
    """Derive a vote from a concern list (conservative, mirrors council).

    - 2+ concerns → veto
    - 1 concern  → hold
    - 0 concerns → proceed
    """
    if len(concerns) >= 2:
        return "veto"
    if concerns:
        return "hold"
    return "proceed"


class Voice:
    """Base protocol for a deterministic squad voice.

    Subclasses set ``name``, ``role`` and ``description``, and must
    implement ``analyze()``. ``analyze_llm()`` defaults to raising
    NotImplementedError — override only when the voice has an LLM path.
    """

    name: str = ""
    role: str = ""
    description: str = ""

    def analyze(self, ctx: dict[str, Any]) -> VoiceResult:
        """Deterministic analysis (stdlib-only). Must be overridden."""
        raise NotImplementedError

    def analyze_llm(
        self,
        ctx: dict[str, Any],
        adapter: _Adapter | None,
    ) -> VoiceResult:
        """Opt-in LLM path. Default: unsupported."""
        raise NotImplementedError(
            f"{self.name} does not support LLM analysis"
        )

    def to_dict(self) -> dict[str, str]:
        """Voice metadata for registry/list operations."""
        return {
            "name": self.name,
            "role": self.role,
            "description": self.description,
        }