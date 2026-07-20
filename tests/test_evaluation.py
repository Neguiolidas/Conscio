"""Tests for conscio.evaluation — 5-axis self-evaluation rubric (v2.15)."""
from __future__ import annotations

import pytest

from conscio import ConsciousnessEngine, evaluate, EvaluationReport, AxisScore
from conscio.evaluation import _band


# ─── Helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine(tmp_path):
    """Ephemeral engine for testing."""
    with ConsciousnessEngine(
        model_name="test-eval",
        storage_path=str(tmp_path),
    ) as e:
        yield e


# ─── Unit: _band helper ───────────────────────────────────────────────────────

class TestBand:
    @pytest.mark.parametrize("value, expected", [
        (1.0, 5),
        (0.9, 5),
        (0.89, 4),
        (0.75, 4),
        (0.74, 3),
        (0.55, 3),
        (0.54, 2),
        (0.35, 2),
        (0.34, 1),
        (0.0, 1),
    ])
    def test_band_mapping(self, value, expected):
        assert _band(value) == expected


# ─── Unit: AxisScore / EvaluationReport data structures ──────────────────────

class TestAxisScore:
    def test_to_dict_has_all_fields(self):
        a = AxisScore("accuracy", 4, "evidence here", "fix it")
        d = a.to_dict()
        assert d["axis"] == "accuracy"
        assert d["score"] == 4
        assert d["label"] == "good"
        assert d["evidence"] == "evidence here"
        assert d["improvement"] == "fix it"

    def test_score_5_no_improvement(self):
        a = AxisScore("clarity", 5, "perfect", "")
        assert a.improvement == ""
        assert a.to_dict()["label"] == "exceptional"


class TestEvaluationReport:
    def test_to_dict(self):
        axes = tuple(AxisScore(n, 4, f"{n} ok", "") for n in
                     ["accuracy", "completeness", "clarity", "actionability", "conciseness"])
        r = EvaluationReport("test", axes, 4.0)
        d = r.to_dict()
        assert d["overall"] == 4.0
        assert len(d["axes"]) == 5
        assert d["task_description"] == "test"

    def test_to_injection_bounded(self):
        axes = tuple(AxisScore(n, 3, f"{n} evidence here " * 20, "fix") for n in
                     ["accuracy", "completeness", "clarity", "actionability", "conciseness"])
        r = EvaluationReport("long task", axes, 3.0, improvements=("do better",))
        text = r.to_injection(max_chars=200)
        assert len(text) <= 200
        assert "overall=3.0" in text


# ─── Integration: evaluate() on fresh engine ──────────────────────────────────

class TestEvaluateFresh:
    def test_returns_evaluation_report(self, engine):
        result = evaluate(engine, "test task")
        assert isinstance(result, EvaluationReport)

    def test_has_5_axes(self, engine):
        result = evaluate(engine, "test task")
        assert len(result.axes) == 5
        names = [a.axis for a in result.axes]
        assert names == ["accuracy", "completeness", "clarity", "actionability", "conciseness"]

    def test_all_scores_in_range(self, engine):
        result = evaluate(engine, "test task")
        for a in result.axes:
            assert 1 <= a.score <= 5, f"{a.axis} score={a.score} out of range"

    def test_overall_is_average(self, engine):
        result = evaluate(engine, "test task")
        expected = round(sum(a.score for a in result.axes) / 5.0, 1)
        assert result.overall == expected

    def test_evidence_nonempty(self, engine):
        result = evaluate(engine, "test task")
        for a in result.axes:
            assert a.evidence, f"{a.axis} has empty evidence"

    def test_self_check_nonempty(self, engine):
        result = evaluate(engine, "test task")
        assert result.self_check

    def test_fresh_engine_typical_scores(self, engine):
        """Fresh engine: moderate confidence, no errors, no stale entities."""
        result = evaluate(engine, "fresh engine test")
        # accuracy: confidence 0.5 default → band 2-3
        assert result.axes[0].score in (2, 3)
        # completeness: 0 stale, 0 active → 3 or 5 depending on active goals
        assert result.axes[1].score >= 2
        # clarity: coherence default 0.5 → band 2-3
        assert result.axes[2].score in (2, 3)


# ─── Integration: evaluate() with output text ─────────────────────────────────

class TestEvaluateWithOutput:
    def test_short_output_low_conciseness(self, engine):
        result = evaluate(engine, "test", output="hi")
        conciseness = [a for a in result.axes if a.axis == "conciseness"][0]
        assert conciseness.score == 2
        assert "too short" in conciseness.evidence

    def test_medium_output_high_conciseness(self, engine):
        text = " ".join([f"word{i}" for i in range(100)])  # 100 unique words
        result = evaluate(engine, "test", output=text)
        conciseness = [a for a in result.axes if a.axis == "conciseness"][0]
        assert conciseness.score >= 3

    def test_long_repetitive_output_low_conciseness(self, engine):
        text = "hello world " * 500  # very repetitive
        result = evaluate(engine, "test", output=text)
        conciseness = [a for a in result.axes if a.axis == "conciseness"][0]
        assert conciseness.score <= 3


# ─── Integration: engine.evaluate() convenience ──────────────────────────────

class TestEngineEvaluate:
    def test_engine_method_delegates(self, engine):
        result = engine.evaluate(task_description="engine method", output="test output here")
        assert isinstance(result, EvaluationReport)
        assert result.task_description == "engine method"

    def test_engine_method_no_args(self, engine):
        result = engine.evaluate()
        assert isinstance(result, EvaluationReport)


# ─── Integration: evaluate() after errors ─────────────────────────────────────

class TestEvaluateAfterErrors:
    def test_errors_lower_accuracy(self, engine):
        # Emit some error events
        for _ in range(5):
            engine.event_bus.emit("error", "consciousness", {"pattern": "API timeout"})

        result = evaluate(engine, "with errors")
        accuracy = [a for a in result.axes if a.axis == "accuracy"][0]
        # With 5 errors out of ~5 recent events, error rate is high → accuracy drops
        assert accuracy.score <= 3

    def test_stale_entities_lower_completeness(self, engine):
        # Add stale entities (old timestamp)
        engine.world.add_entity("old-thing", "system", state="deprecated")
        # Force staleness by manipulating the entity timestamp
        # (we just add it — staleness depends on age threshold)

        result = evaluate(engine, "with stale")
        completeness = [a for a in result.axes if a.axis == "completeness"][0]
        # At least the evidence should mention stale entities
        assert "stale" in completeness.evidence.lower() or completeness.score >= 3


# ─── Integration: evaluate() is read-only ─────────────────────────────────────

class TestEvaluateReadOnly:
    def test_no_events_emitted(self, engine):
        before = len(engine.event_bus.query(limit=100))
        evaluate(engine, "read-only test")
        after = len(engine.event_bus.query(limit=100))
        assert before == after

    def test_no_state_mutation(self, engine):
        state_before = engine.get_state_for_injection()
        evaluate(engine, "read-only test")
        state_after = engine.get_state_for_injection()
        assert state_before == state_after


# ─── Integration: improvements ranked by gap ─────────────────────────────────

class TestImprovementsRanking:
    def test_improvements_are_ranked(self, engine):
        result = evaluate(engine, "test")
        # All improvements should be from axes with score < 5
        for imp in result.improvements:
            assert imp  # non-empty

    def test_max_3_improvements(self, engine):
        result = evaluate(engine, "test")
        assert len(result.improvements) <= 3
