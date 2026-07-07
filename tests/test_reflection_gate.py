"""Tests for ReflectionGate — adaptive reflection depth gate."""
from __future__ import annotations

import pytest

from conscio.reflection_gate import (
    GateContext,
    GateDecision,
    ReflectionGate,
    ConfidenceHeuristic,
    CoherenceHeuristic,
    ContradictionHeuristic,
    NoveltyHeuristic,
)


# ── GateContext ──────────────────────────────────────────────────────────


class TestGateContext:
    def test_default_context(self):
        ctx = GateContext()
        assert ctx.confidence == 0.5
        assert ctx.coherence == 0.5
        assert ctx.contradiction_count == 0
        assert ctx.novelty_count == 0
        assert ctx.metabolic == ""
        assert ctx.cycle == 0

    def test_custom_context(self):
        ctx = GateContext(
            confidence=0.9,
            coherence=0.8,
            contradiction_count=1,
            novelty_count=3,
            metabolic="VITAL",
            cycle=2,
        )
        assert ctx.confidence == 0.9
        assert ctx.coherence == 0.8
        assert ctx.contradiction_count == 1
        assert ctx.novelty_count == 3
        assert ctx.metabolic == "VITAL"
        assert ctx.cycle == 2

    def test_frozen(self):
        ctx = GateContext()
        with pytest.raises(AttributeError):
            ctx.confidence = 0.1  # type: ignore[misc]


# ── GateDecision ─────────────────────────────────────────────────────────


class TestGateDecision:
    def test_decision_shape(self):
        d = GateDecision(
            cycles=2,
            continue_reflection=True,
            need_score=0.6,
            breakdown={"confidence": 0.1},
            reason="low confidence",
        )
        assert d.cycles == 2
        assert d.continue_reflection is True
        assert d.need_score == 0.6
        assert d.breakdown == {"confidence": 0.1}
        assert d.reason == "low confidence"

    def test_frozen(self):
        d = GateDecision(
            cycles=1,
            continue_reflection=False,
            need_score=0.0,
            breakdown={},
            reason="ok",
        )
        with pytest.raises(AttributeError):
            d.cycles = 5  # type: ignore[misc]


# ── Heuristics ───────────────────────────────────────────────────────────


class TestConfidenceHeuristic:
    def test_high_confidence_low_need(self):
        h = ConfidenceHeuristic()
        ctx = GateContext(confidence=0.9)
        assert h.score(ctx) == pytest.approx(0.1)

    def test_low_confidence_high_need(self):
        h = ConfidenceHeuristic()
        ctx = GateContext(confidence=0.2)
        assert h.score(ctx) == pytest.approx(0.8)

    def test_zero_confidence_max_need(self):
        h = ConfidenceHeuristic()
        ctx = GateContext(confidence=0.0)
        assert h.score(ctx) == pytest.approx(1.0)

    def test_perfect_confidence_zero_need(self):
        h = ConfidenceHeuristic()
        ctx = GateContext(confidence=1.0)
        assert h.score(ctx) == pytest.approx(0.0)

    def test_name(self):
        assert ConfidenceHeuristic().name == "confidence"

    def test_available(self):
        assert ConfidenceHeuristic().available() is True


class TestCoherenceHeuristic:
    def test_high_coherence_low_need(self):
        h = CoherenceHeuristic()
        ctx = GateContext(coherence=0.85)
        assert h.score(ctx) == pytest.approx(0.15)

    def test_low_coherence_high_need(self):
        h = CoherenceHeuristic()
        ctx = GateContext(coherence=0.3)
        assert h.score(ctx) == pytest.approx(0.7)

    def test_name(self):
        assert CoherenceHeuristic().name == "coherence"

    def test_available(self):
        assert CoherenceHeuristic().available() is True


class TestContradictionHeuristic:
    def test_zero_contradictions(self):
        h = ContradictionHeuristic()
        ctx = GateContext(contradiction_count=0)
        assert h.score(ctx) == 0.0

    def test_one_contradiction(self):
        h = ContradictionHeuristic()
        ctx = GateContext(contradiction_count=1)
        assert h.score(ctx) == pytest.approx(1 / 3)

    def test_three_contradictions_max(self):
        h = ContradictionHeuristic()
        ctx = GateContext(contradiction_count=3)
        assert h.score(ctx) == 1.0

    def test_six_contradictions_capped(self):
        h = ContradictionHeuristic()
        ctx = GateContext(contradiction_count=6)
        assert h.score(ctx) == 1.0  # capped

    def test_name(self):
        assert ContradictionHeuristic().name == "contradiction"

    def test_available(self):
        assert ContradictionHeuristic().available() is True


class TestNoveltyHeuristic:
    def test_zero_novelty(self):
        h = NoveltyHeuristic()
        ctx = GateContext(novelty_count=0)
        assert h.score(ctx) == 0.0

    def test_five_novelty_max(self):
        h = NoveltyHeuristic()
        ctx = GateContext(novelty_count=5)
        assert h.score(ctx) == 1.0

    def test_ten_novelty_capped(self):
        h = NoveltyHeuristic()
        ctx = GateContext(novelty_count=10)
        assert h.score(ctx) == 1.0  # capped

    def test_name(self):
        assert NoveltyHeuristic().name == "novelty"

    def test_available(self):
        assert NoveltyHeuristic().available() is True


# ── ReflectionGate.decide() ──────────────────────────────────────────────


class TestReflectionGateDecide:
    def test_easy_case_one_cycle(self):
        """High confidence + high coherence → 1 cycle."""
        gate = ReflectionGate(max_cycles=3, threshold=0.5)
        ctx = GateContext(confidence=0.9, coherence=0.85, cycle=0)
        decision = gate.decide(ctx)
        assert decision.cycles == 1
        assert decision.continue_reflection is False

    def test_hard_case_multiple_cycles(self):
        """Low confidence + low coherence → multiple cycles."""
        gate = ReflectionGate(max_cycles=3, threshold=0.5)
        ctx = GateContext(
            confidence=0.2,
            coherence=0.3,
            contradiction_count=2,
            novelty_count=4,
            cycle=0,
        )
        decision = gate.decide(ctx)
        assert decision.cycles >= 2
        assert decision.continue_reflection is True

    def test_max_cycles_cap(self):
        """Never exceed max_cycles."""
        gate = ReflectionGate(max_cycles=2, threshold=0.1)
        ctx = GateContext(
            confidence=0.0,
            coherence=0.0,
            contradiction_count=10,
            novelty_count=10,
            cycle=0,
        )
        decision = gate.decide(ctx)
        assert decision.cycles <= 2

    def test_always_at_least_one_cycle(self):
        """Even with perfect scores, at least 1 cycle."""
        gate = ReflectionGate(max_cycles=3, threshold=0.5)
        ctx = GateContext(
            confidence=1.0,
            coherence=1.0,
            contradiction_count=0,
            novelty_count=0,
            cycle=0,
        )
        decision = gate.decide(ctx)
        assert decision.cycles >= 1

    def test_per_cycle_stops_early(self):
        """After cycle 1, if confidence is high, stop."""
        gate = ReflectionGate(max_cycles=3, threshold=0.5)
        ctx = GateContext(confidence=0.9, coherence=0.85, cycle=1)
        decision = gate.decide(ctx)
        assert decision.continue_reflection is False

    def test_per_cycle_continues(self):
        """After cycle 1, if still low, continue."""
        gate = ReflectionGate(max_cycles=3, threshold=0.5)
        # confidence=0.1 → 0.9, coherence=0.1 → 0.9
        # need = 0.35*0.9 + 0.25*0.9 + 0 + 0 = 0.54 >= 0.5
        ctx = GateContext(confidence=0.1, coherence=0.1, cycle=1)
        decision = gate.decide(ctx)
        assert decision.continue_reflection is True

    def test_per_cycle_at_max_cap(self):
        """At max_cycles, stop even if need is high."""
        gate = ReflectionGate(max_cycles=2, threshold=0.1)
        ctx = GateContext(confidence=0.1, coherence=0.1, cycle=2)
        decision = gate.decide(ctx)
        assert decision.continue_reflection is False

    def test_breakdown_in_decision(self):
        gate = ReflectionGate(max_cycles=3, threshold=0.5)
        ctx = GateContext(confidence=0.5, coherence=0.5, cycle=0)
        decision = gate.decide(ctx)
        assert "confidence" in decision.breakdown
        assert "coherence" in decision.breakdown
        assert "contradiction" in decision.breakdown
        assert "novelty" in decision.breakdown

    def test_reason_in_decision(self):
        gate = ReflectionGate(max_cycles=3, threshold=0.5)
        ctx = GateContext(confidence=0.2, coherence=0.3, cycle=0)
        decision = gate.decide(ctx)
        assert len(decision.reason) > 0

    def test_need_score_between_zero_and_one(self):
        gate = ReflectionGate(max_cycles=3, threshold=0.5)
        for conf in [0.0, 0.25, 0.5, 0.75, 1.0]:
            for coh in [0.0, 0.5, 1.0]:
                ctx = GateContext(confidence=conf, coherence=coh, cycle=0)
                decision = gate.decide(ctx)
                assert 0.0 <= decision.need_score <= 1.0

    def test_custom_weights(self):
        """Only confidence matters when other weights are 0."""
        gate = ReflectionGate(
            max_cycles=3,
            threshold=0.5,
            weights={
                "confidence": 1.0,
                "coherence": 0.0,
                "contradiction": 0.0,
                "novelty": 0.0,
            },
        )
        ctx = GateContext(
            confidence=0.9,
            coherence=0.0,
            contradiction_count=10,
            novelty_count=10,
            cycle=0,
        )
        decision = gate.decide(ctx)
        assert decision.cycles == 1  # high confidence → stop

    def test_weights_normalized(self):
        """Weights don't need to sum to 1.0."""
        gate = ReflectionGate(
            max_cycles=3,
            threshold=0.5,
            weights={
                "confidence": 2.0,
                "coherence": 2.0,
                "contradiction": 2.0,
                "novelty": 2.0,
            },
        )
        ctx = GateContext(confidence=0.5, coherence=0.5, cycle=0)
        decision = gate.decide(ctx)
        assert 0.0 <= decision.need_score <= 1.0

    def test_custom_threshold(self):
        """Low threshold → stop sooner."""
        gate = ReflectionGate(max_cycles=3, threshold=0.2)
        ctx = GateContext(confidence=0.8, coherence=0.8, cycle=0)
        # need_score ≈ 0.35*0.2 + 0.25*0.2 = 0.12 < 0.2 → stop
        decision = gate.decide(ctx)
        assert decision.continue_reflection is False

    def test_heuristic_failure_fallback(self):
        """If a heuristic raises, it should fall back to 0.5."""

        class BrokenHeuristic:
            name = "broken"
            def available(self) -> bool:
                return True
            def score(self, ctx: GateContext) -> float:
                raise RuntimeError("boom")

        gate = ReflectionGate(max_cycles=3, threshold=0.5)
        gate._heuristics = [BrokenHeuristic()]  # type: ignore[assignment]
        # Need to also add the name to weights
        gate._weights = {"broken": 1.0}
        ctx = GateContext(cycle=0)
        decision = gate.decide(ctx)
        assert decision.cycles >= 1  # never returns 0
        assert decision.need_score == pytest.approx(0.5)

    def test_default_max_cycles_is_3(self):
        gate = ReflectionGate()
        assert gate.max_cycles == 3

    def test_default_threshold_is_05(self):
        gate = ReflectionGate()
        assert gate.threshold == 0.5

    def test_max_cycles_at_least_1(self):
        gate = ReflectionGate(max_cycles=0)
        assert gate.max_cycles == 1

    def test_max_cycles_negative(self):
        gate = ReflectionGate(max_cycles=-5)
        assert gate.max_cycles == 1
