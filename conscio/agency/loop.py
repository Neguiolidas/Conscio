# conscio/agency/loop.py
"""
GoalArbiter + AutonomyLoop (spec section 5.11).

The arbiter picks the cycle's goal deterministically (no LLM): the
GoalGenerator's priority order x alignment with the dominant dissonance
(P4) x out of quarantine. The loop is the L3 heartbeat —
reflect -> arbiter/act -> ledger -> (dream when recommended) — repeated
until the metabolic ActBudget is exhausted. The budget is a binding
execution gate (P3), not advisory.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from conscio.metabolic import MetabolicContext, MetabolicState

from .act import ActReport, ActStatus, goal_fingerprint

# Lexical hints linking a coherence dimension to goal verbs (P4).
DISSONANCE_HINTS: dict[str, tuple[str, ...]] = {
    "epistemic": ("investigate", "verify", "learn", "understand",
                  "confidence"),
    "reality": ("check", "monitor", "observe", "perceive", "status"),
    "ontological": ("reconcile", "contradiction", "consistency", "conflict"),
    "temporal": ("stale", "prune", "refresh", "update", "expire"),
}
ALIGNMENT_BONUS = 2.0


class GoalArbiter:
    """Deterministic goal selection for one act() cycle."""

    def __init__(self, breaker: Any):
        self.breaker = breaker

    def choose(self, state: Any) -> str | None:
        self.breaker.review_quarantine()
        goals = [g for g in state.active_goals
                 if not self.breaker.is_quarantined(goal_fingerprint(g))]
        if not goals:
            return None
        hints = DISSONANCE_HINTS.get((state.coherence_note or "").lower(), ())

        def score(item: tuple[int, str]) -> float:
            index, goal = item
            base = float(len(goals) - index)     # generator priority order
            aligned = any(h in goal.lower() for h in hints)
            return base + (ALIGNMENT_BONUS if aligned else 0.0)

        return max(enumerate(goals), key=score)[1]
