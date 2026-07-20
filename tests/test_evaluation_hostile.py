"""Regression tests for hostile-review-discovered bugs in conscio.evaluation.

Each test documents a bug found during hostile review and confirms the fix.
"""
from __future__ import annotations

import pytest

from conscio import ConsciousnessEngine, evaluate


@pytest.fixture
def engine(tmp_path):
    with ConsciousnessEngine(
        model_name="test-hostile",
        storage_path=str(tmp_path),
    ) as e:
        yield e


# ─── Bug 1: completeness evidence misleading ───────────────────────────────

class TestCompletenessEvidence:
    """BUG: evidence said 'indicates deferred cleanup' when stale=0.
    FIX: evidence now describes the actual bottleneck."""

    def test_zero_stale_zero_active_says_no_commitment(self, engine):
        result = evaluate(engine, "fresh")
        comp = [a for a in result.axes if a.axis == "completeness"][0]
        assert comp.score == 3
        assert "no stale entities" in comp.evidence
        assert "0 active goal(s)" in comp.evidence
        # Must NOT say "indicates deferred cleanup" when stale=0
        assert "deferred cleanup" not in comp.evidence

    def test_zero_stale_with_active_goals_scores_5(self, engine):
        # Generate a goal
        engine.goals.generate_from_curiosity("test anomaly", context="test")
        result = evaluate(engine, "with goals")
        comp = [a for a in result.axes if a.axis == "completeness"][0]
        assert comp.score == 5
        assert "active goal" in comp.evidence


# ─── Bug 2: conciseness penalizes unique long text ────────────────────────

class TestConcisenessLongUniqueText:
    """BUG: 5000 unique words scored 2 ("too verbose").
    FIX: long non-repetitive text scored 3 (may be justified)."""

    def test_5000_unique_words_not_scored_2(self, engine):
        text = " ".join([f"word{i}" for i in range(5000)])
        result = evaluate(engine, "long task", output=text)
        conc = [a for a in result.axes if a.axis == "conciseness"][0]
        assert conc.score >= 3, f"5000 unique words should not score below 3, got {conc.score}"
        assert "redundant" in conc.evidence or "very long" in conc.evidence

    def test_5000_repetitive_words_scored_2(self, engine):
        text = "hello world " * 2500  # 5000 words, high repetition
        result = evaluate(engine, "repetitive task", output=text)
        conc = [a for a in result.axes if a.axis == "conciseness"][0]
        assert conc.score == 2
        assert "redundancy" in conc.evidence


# ─── Bug 3: contradictions not detected (state_log approach) ──────────────

class TestContradictionDetection:
    """BUG: _count_contradictions compared names not states.
    FIX: evaluation.py now checks state_log for multiple distinct states."""

    def test_contradiction_from_state_history(self, engine):
        # Add entity, then change its state
        engine.world.add_entity("the-sky", "observation", state="blue")
        engine.world.add_entity("the-sky", "observation", state="red")

        result = evaluate(engine, "contradiction test")
        clarity = [a for a in result.axes if a.axis == "clarity"][0]
        assert "1 contradiction" in clarity.evidence

    def test_no_contradiction_single_state(self, engine):
        engine.world.add_entity("the-sky", "observation", state="blue")

        result = evaluate(engine, "no contradiction")
        clarity = [a for a in result.axes if a.axis == "clarity"][0]
        assert "no contradictions" in clarity.evidence or "0 contradiction" in clarity.evidence


# ─── Bug 4: evaluate works after engine.close() ──────────────────────────

class TestEvaluateAfterClose:
    """BUG: evaluate() returned stale data after engine.close().
    FIX: engine.evaluate() raises RuntimeError if _closed."""

    def test_evaluate_after_close_raises(self, tmp_path):
        eng = ConsciousnessEngine(
            model_name="test-close",
            storage_path=str(tmp_path),
        )
        eng.close()
        with pytest.raises(RuntimeError, match="closed"):
            eng.evaluate("should fail")

    def test_evaluate_before_close_works(self, tmp_path):
        eng = ConsciousnessEngine(
            model_name="test-close",
            storage_path=str(tmp_path),
        )
        result = eng.evaluate("should work")
        assert result.overall > 0
        eng.close()


# ─── Bug 5 (new): MCP handler with None args ────────────────────────────

class TestMCPHandlerNoneArgs:
    """Edge case: what happens if MCP server calls handler with None args?"""

    def test_evaluate_with_empty_dict(self, engine):
        args = {}
        result = engine.evaluate(
            args.get("task_description", ""),
            args.get("output"),
        )
        # Empty task_description gets replaced with "(unnamed task)" by evaluate()
        assert result.task_description == "(unnamed task)"
