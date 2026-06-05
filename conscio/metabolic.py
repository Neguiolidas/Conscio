"""
MetabolicContext — context-as-life-energy tier model.

Adapted from Noosphere-Manifold's metabolic system (Rule 3: Proactive Phase
Monitoring). Pure and advisory: maps live context usage to a tier and emits
recommendations. It NEVER triggers an action itself — honoring Conscio's
safety rule that goals/recommendations are advisory.

Tiers (by % of context window used):
    VITAL    0–40%   work freely
    ACTIVE   40–50%  consolidate, complete threads
    FATIGUE  50–70%  plan handoff (Mitosis)
    CRITICAL 70%+    transfer now; no new work
"""

from __future__ import annotations

from enum import Enum


class MetabolicState(Enum):
    VITAL = "vital"
    ACTIVE = "active"
    FATIGUE = "fatigue"
    CRITICAL = "critical"


_ACTIONS = {
    MetabolicState.VITAL: "work freely — explore and build understanding",
    MetabolicState.ACTIVE: "consolidate — complete active threads",
    MetabolicState.FATIGUE: "plan handoff — wrap up and prepare a soul package",
    MetabolicState.CRITICAL: "transfer now — finish the atomic task, start nothing new",
}


class MetabolicContext:
    """Pure tier-mapper over context usage. All methods are static."""

    @staticmethod
    def usage_pct(used_tokens: int, context_window: int) -> float:
        """Percentage of context window consumed, clamped to [0, 100]."""
        if context_window <= 0:
            return 0.0
        pct = used_tokens / context_window * 100.0
        return max(0.0, min(pct, 100.0))

    @staticmethod
    def assess(used_tokens: int, context_window: int) -> MetabolicState:
        """Map current usage to a metabolic tier."""
        pct = MetabolicContext.usage_pct(used_tokens, context_window)
        if pct < 40.0:
            return MetabolicState.VITAL
        if pct < 50.0:
            return MetabolicState.ACTIVE
        if pct < 70.0:
            return MetabolicState.FATIGUE
        return MetabolicState.CRITICAL

    @staticmethod
    def tier_action(state: MetabolicState) -> str:
        """Advisory action text for a tier."""
        return _ACTIONS[state]

    @staticmethod
    def should_mitosis(state: MetabolicState) -> bool:
        """Recommend handoff (Mitosis) at FATIGUE or above. Advisory only."""
        return state in (MetabolicState.FATIGUE, MetabolicState.CRITICAL)

    @staticmethod
    def should_dream(state: MetabolicState) -> bool:
        """Recommend a consolidation pass at CRITICAL. Advisory only."""
        return state is MetabolicState.CRITICAL
