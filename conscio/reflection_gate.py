"""ReflectionGate — adaptive reflection depth gate for Conscio.

Origin: Think-Vetor PonderNet concept (CromIA). Reimplemented from scratch.
Decides how many cycles of reflect() to run (1 to max_cycles) based on
textual heuristics computed from existing Conscio state. Does NOT execute
reflections — only decides depth.

License: AGPL-3.0-or-later
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Max entities to check for contradictions (O(n²) cap)
MAX_ENTITIES_FOR_CONTRADICTION = 20


@dataclass(frozen=True)
class GateContext:
    """Input to the gate — snapshot of Conscio state at decision time."""

    confidence: float = 0.5  # meta.average_confidence(), [0, 1]
    coherence: float = 0.5  # coherence_report.score, [0, 1]
    contradiction_count: int = 0  # detected contradiction pairs
    novelty_count: int = 0  # new entities since last cycle
    metabolic: str = ""  # metabolic state label (VITAL/FATIGUE/CRITICAL)
    cycle: int = 0  # current cycle index (0-based)


@dataclass(frozen=True)
class GateDecision:
    """Output of the gate — what to do next."""

    cycles: int  # recommended total cycles (>= 1)
    continue_reflection: bool  # should we do another cycle?
    need_score: float  # weighted need-for-more score [0, 1]
    breakdown: dict[str, float]  # per-heuristic scores
    reason: str  # human-readable explanation


@runtime_checkable
class Heuristic(Protocol):
    """A single heuristic that computes a 'need more reflection' score."""

    name: str

    def score(self, ctx: GateContext) -> float:
        """Return [0, 1] — higher = need more reflection."""
        ...

    def available(self) -> bool:
        """Whether this heuristic has enough data to be meaningful."""
        ...


class ConfidenceHeuristic:
    """Low confidence → need more reflection. Score = 1 - confidence."""

    name = "confidence"

    def score(self, ctx: GateContext) -> float:
        return 1.0 - ctx.confidence

    def available(self) -> bool:
        return True


class CoherenceHeuristic:
    """Low coherence → need more reflection. Score = 1 - coherence."""

    name = "coherence"

    def score(self, ctx: GateContext) -> float:
        return 1.0 - ctx.coherence

    def available(self) -> bool:
        return True


class ContradictionHeuristic:
    """More contradictions → need more reflection. Normalized: 3+ → 1.0."""

    name = "contradiction"

    def score(self, ctx: GateContext) -> float:
        return min(1.0, ctx.contradiction_count / 3.0)

    def available(self) -> bool:
        return True


class NoveltyHeuristic:
    """More new entities → need more reflection. Normalized: 5+ → 1.0."""

    name = "novelty"

    def score(self, ctx: GateContext) -> float:
        return min(1.0, ctx.novelty_count / 5.0)

    def available(self) -> bool:
        return True


_DEFAULT_WEIGHTS: dict[str, float] = {
    "confidence": 0.35,
    "coherence": 0.25,
    "contradiction": 0.25,
    "novelty": 0.15,
}


class ReflectionGate:
    """Decides how many cycles of reflect() to run.

    Pure function of GateContext — does NOT modify any state.
    Failure of any heuristic → 0.5 fallback. Gate failure → 1 cycle.
    """

    def __init__(
        self,
        max_cycles: int = 3,
        threshold: float = 0.5,
        weights: dict[str, float] | None = None,
    ):
        self.max_cycles = max(1, max_cycles)
        self.threshold = threshold
        self._raw_weights = weights if weights is not None else dict(_DEFAULT_WEIGHTS)
        self._weights = self._normalize_weights(self._raw_weights)
        self._heuristics: list[Heuristic] = [
            ConfidenceHeuristic(),
            CoherenceHeuristic(),
            ContradictionHeuristic(),
            NoveltyHeuristic(),
        ]

    @staticmethod
    def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
        """Normalize weights to sum to 1.0."""
        total = sum(weights.values())
        if total <= 0:
            return dict(_DEFAULT_WEIGHTS)
        return {k: v / total for k, v in weights.items()}

    def _score_heuristic(self, h: Heuristic, ctx: GateContext) -> float:
        """Score a single heuristic with fallback on failure."""
        try:
            if not h.available():
                return 0.5
            val = h.score(ctx)
            # Clamp to [0, 1]
            return max(0.0, min(1.0, val))
        except Exception as e:
            logger.debug("reflection_gate: heuristic %s failed: %s", h.name, e)
            return 0.5

    def decide(self, ctx: GateContext) -> GateDecision:
        """Decide whether to continue reflection and how many cycles total."""
        breakdown: dict[str, float] = {}
        for h in self._heuristics:
            breakdown[h.name] = self._score_heuristic(h, ctx)

        need_score = sum(
            self._weights.get(name, 0.0) * score
            for name, score in breakdown.items()
        )
        need_score = max(0.0, min(1.0, need_score))

        # Cycle 0: always at least 1 cycle (short-circuit)
        if ctx.cycle == 0:
            if need_score >= self.threshold:
                # Scale: high need → up to max_cycles
                cycles = max(1, min(self.max_cycles, int(need_score * self.max_cycles) + 1))
            else:
                cycles = 1
            continue_reflection = cycles > 1
            reason = f"cycle=0 need={need_score:.3f} → {cycles} cycles"
            return GateDecision(
                cycles=cycles,
                continue_reflection=continue_reflection,
                need_score=round(need_score, 4),
                breakdown={k: round(v, 4) for k, v in breakdown.items()},
                reason=reason,
            )

        # Per-cycle: should we continue?
        at_cap = ctx.cycle >= self.max_cycles
        should_continue = need_score >= self.threshold and not at_cap
        reason = (
            f"cycle={ctx.cycle} need={need_score:.3f} "
            f"threshold={self.threshold} → "
            f"{'continue' if should_continue else 'stop'}"
            f"{' (max cap)' if at_cap else ''}"
        )
        return GateDecision(
            cycles=self.max_cycles,
            continue_reflection=should_continue,
            need_score=round(need_score, 4),
            breakdown={k: round(v, 4) for k, v in breakdown.items()},
            reason=reason,
        )
