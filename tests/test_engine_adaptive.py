"""Integration tests for adaptive reflection in ConsciousnessEngine."""
from __future__ import annotations

import os

import pytest

from conscio.engine import ConsciousnessEngine


class TestAdaptiveReflectionFlag:
    def test_default_off(self, tmp_path):
        """adaptive_reflection defaults to False — zero behavior change."""
        eng = ConsciousnessEngine(
            model_name="test-model",
            storage_path=tmp_path,
        )
        assert eng.adaptive_reflection is False
        assert eng.reflection_gate is None
        eng.close()

    def test_explicit_on(self, tmp_path):
        eng = ConsciousnessEngine(
            model_name="test-model",
            storage_path=tmp_path,
            adaptive_reflection=True,
            max_reflection_cycles=3,
        )
        assert eng.adaptive_reflection is True
        assert eng.reflection_gate is not None
        assert eng.reflection_gate.max_cycles == 3
        eng.close()

    def test_custom_max_cycles(self, tmp_path):
        eng = ConsciousnessEngine(
            model_name="test-model",
            storage_path=tmp_path,
            adaptive_reflection=True,
            max_reflection_cycles=5,
        )
        assert eng.reflection_gate.max_cycles == 5
        eng.close()

    def test_off_no_gate_events(self, tmp_path):
        """reflect() with adaptive off → no reflection_gate events."""
        eng = ConsciousnessEngine(
            model_name="test-model",
            storage_path=tmp_path,
            adaptive_reflection=False,
        )
        eng.reflect(world_state="test")
        gate_events = eng.event_bus.query(type="reflection_gate", limit=10)
        assert len(gate_events) == 0
        eng.close()


class TestAdaptiveReflectBehavior:
    def test_reflect_without_adapter(self, tmp_path):
        """reflect() with adaptive on but no LLM adapter — still works."""
        eng = ConsciousnessEngine(
            model_name="test-model",
            storage_path=tmp_path,
            adaptive_reflection=True,
            max_reflection_cycles=2,
        )
        result = eng.reflect(world_state="test state")
        assert isinstance(result, dict)
        assert "meta_confidence" in result
        eng.close()

    def test_reflect_emits_gate_event(self, tmp_path):
        """reflect() with adaptive on emits reflection_gate event."""
        eng = ConsciousnessEngine(
            model_name="test-model",
            storage_path=tmp_path,
            adaptive_reflection=True,
            max_reflection_cycles=3,
        )
        eng.reflect(world_state="test")
        events = eng.event_bus.query(type="reflection_gate", limit=10)
        # At least 1 gate event (from cycle 1 evaluation)
        assert len(events) >= 1
        event_data = events[0].to_dict()["data"]
        assert "need_score" in event_data
        assert "continue" in event_data
        assert "breakdown" in event_data
        eng.close()

    def test_adaptive_off_legacy_behavior(self, tmp_path):
        """reflect() with adaptive off behaves exactly like before."""
        eng_off = ConsciousnessEngine(
            model_name="test-model",
            storage_path=tmp_path / "off",
            adaptive_reflection=False,
        )
        result_off = eng_off.reflect(world_state="test")
        eng_off.close()

        eng_on = ConsciousnessEngine(
            model_name="test-model",
            storage_path=tmp_path / "on",
            adaptive_reflection=True,
            max_reflection_cycles=1,
        )
        result_on = eng_on.reflect(world_state="test")
        eng_on.close()

        # Both should return dicts with the same keys
        assert set(result_off.keys()) == set(result_on.keys())

    def test_build_gate_context(self, tmp_path):
        """_build_gate_context returns valid GateContext."""
        eng = ConsciousnessEngine(
            model_name="test-model",
            storage_path=tmp_path,
            adaptive_reflection=True,
            max_reflection_cycles=3,
        )
        eng.reflect(world_state="test")  # populate state
        ctx = eng._build_gate_context(cycle=1)
        assert 0.0 <= ctx.confidence <= 1.0
        assert 0.0 <= ctx.coherence <= 1.0
        assert ctx.contradiction_count >= 0
        assert ctx.novelty_count >= 0
        assert ctx.cycle == 1
        eng.close()

    def test_count_contradictions_empty(self, tmp_path):
        """_count_contradictions with empty list → 0."""
        eng = ConsciousnessEngine(
            model_name="test-model",
            storage_path=tmp_path,
            adaptive_reflection=True,
        )
        assert eng._count_contradictions([]) == 0
        eng.close()

    def test_count_contradictions_single(self, tmp_path):
        """_count_contradictions with 1 entity → 0 (no pairs)."""
        eng = ConsciousnessEngine(
            model_name="test-model",
            storage_path=tmp_path,
            adaptive_reflection=True,
        )
        assert eng._count_contradictions([{"name": "a"}]) == 0
        eng.close()


@pytest.mark.skipif(
    not os.environ.get("CONSCIO_SMOKE_TEST"),
    reason="Set CONSCIO_SMOKE_TEST=1 to run LM Studio smoke tests",
)
class TestSmokeQwenAdaptive:
    """Smoke tests against LM Studio with qwen3.5-0.8b loaded.

    Requires: LM Studio running at localhost:1234 with qwen3.5-0.8b.
    Set CONSCIO_SMOKE_TEST=1 to enable.
    """

    def test_adaptive_off_one_cycle(self, tmp_path):
        """adaptive_reflection=False → exactly 1 reflect cycle (legacy)."""
        eng = ConsciousnessEngine(
            model_name="qwen3.5-0.8b",
            storage_path=tmp_path,
            adaptive_reflection=False,
            base_url="http://localhost:1234/v1",
        )
        eng.reflect(world_state="test question")
        gate_events = eng.event_bus.query(type="reflection_gate", limit=10)
        assert len(gate_events) == 0  # no gate events when off
        eng.close()
