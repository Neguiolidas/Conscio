"""Typed decisions Choice/Score/Noul (v4.9 item 13; openjev/types.py port).

Stdlib only. The Jev limits (255 options, 10 score levels) are the
original's; confidence_from_probs generalizes (max_p*K-1)/(K-1) to any
K>=2: uniform -> 0, one-hot -> 1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_OPTIONS = 255
MAX_SCORE_LEVELS = 10


@dataclass(frozen=True)
class Choice:
    options: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 2 <= len(self.options) <= MAX_OPTIONS:
            raise ValueError(
                f"choice needs 2..{MAX_OPTIONS} options, got {len(self.options)}")
        if len(set(self.options)) != len(self.options):
            raise ValueError("choice options must be distinct")

    def validate(self) -> Choice:
        return self


@dataclass(frozen=True)
class Score:
    levels: int

    def __post_init__(self) -> None:
        if not 2 <= self.levels <= MAX_SCORE_LEVELS:
            raise ValueError(
                f"score needs 2..{MAX_SCORE_LEVELS} levels, got {self.levels}")

    def validate(self) -> Score:
        return self


@dataclass(frozen=True)
class Noul:
    """Binary yes/no question (no confidence: the answer IS the probability)."""
    question_hint: str = ""

    def validate(self) -> Noul:
        return self


def confidence_from_probs(probs: list[float]) -> float:
    """(max_p*K-1)/(K-1), generalized to any K>=2 (openjev formula).

    Uniform -> 0; one-hot -> 1. Clamps to [0, 1] for float noise.
    """
    if not probs:
        return 0.0
    k = len(probs)
    if k < 2:
        return 1.0
    value = (max(probs) * k - 1) / (k - 1)
    return max(0.0, min(1.0, value))


def weighted_score(probs: list[float]) -> float:
    """Mean weighted by index (score levels 1..K)."""
    if not probs:
        return 0.0
    total = sum((i + 1) * p for i, p in enumerate(probs))
    return total / sum(probs) if sum(probs) else 0.0


def render_answer(question: str, kind: str, probs: list[float],
                  options: list[str] | None = None) -> dict[str, Any]:
    """Jev-shaped answer: choice/score carry confidence, noul does not."""
    if kind == "noul":
        p = probs[0] if probs else 0.0
        return {"question": question, "type": "noul", "noul": p}
    return {"question": question, "type": kind,
            "probabilities": probs,
            "confidence": confidence_from_probs(probs),
            **({"options": options} if options else {})}


@dataclass(frozen=True)
class SystemOneRequest:
    state: str
    questions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def entropy_nats(self) -> float:
        """Shannon entropy of the state string (diagnostic only)."""
        if not self.state:
            return 0.0
        import math
        from collections import Counter
        counts = Counter(self.state)
        n = len(self.state)
        return -sum((c / n) * math.log2(c / n) for c in counts.values())
