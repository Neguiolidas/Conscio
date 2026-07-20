"""Tests for diagnostics module — context_budget, eval_harness, rules_distill."""

from __future__ import annotations

import tempfile

import pytest

from conscio import ConsciousnessEngine
from conscio.diagnostics import (
    context_budget, eval_harness, rules_distill,
    EVAL_CAPABILITY, EVAL_REGRESSION, EVAL_BENCHMARK,
    _pass_at_k,
)


@pytest.fixture
def engine(tmp_path):
    with ConsciousnessEngine(model_name="test", storage_path=str(tmp_path)) as e:
        yield e


# ── context_budget ───────────────────────────────────────────────────

class TestContextBudget:

    def test_basic_audit(self, engine):
        r = context_budget(engine, context_tokens=50000, context_window=200000)
        assert r["token_pressure"] == 0.25
        assert r["headroom_pct"] == 75.0
        assert "recommendations" in r

    def test_high_pressure_warning(self, engine):
        r = context_budget(engine, context_tokens=180000, context_window=200000)
        assert r["token_pressure"] > 0.8
        assert any(rec["priority"] == "critical" for rec in r["recommendations"])

    def test_full_detail(self, engine):
        r = context_budget(engine, context_tokens=1000, context_window=200000,
                           detail="full")
        assert "source_breakdown" in r

    def test_auto_detect_tokens(self, engine):
        r = context_budget(engine)
        assert "token_pressure" in r
        assert r["context_window"] > 0

    def test_event_categories(self, engine):
        r = context_budget(engine, context_tokens=1000, context_window=200000)
        assert "event_categories" in r
        assert isinstance(r["event_categories"], dict)

    def test_metabolic_tiers(self, engine):
        r = context_budget(engine, context_tokens=1000, context_window=200000)
        assert "metabolic_tiers" in r
        assert "vital" in r["metabolic_tiers"]

    def test_emits_budget_event(self, engine):
        context_budget(engine, context_tokens=1000, context_window=200000)
        events = engine.event_bus.query(type="diagnostic:budget")
        assert len(events) >= 1

    def test_closed_engine_raises(self, tmp_path):
        e = ConsciousnessEngine(model_name="t", storage_path=str(tmp_path))
        e.close()
        with pytest.raises(RuntimeError):
            context_budget(e)


# ── eval_harness ──────────────────────────────────────────────────────

class TestEvalHarness:

    def test_define_eval(self, engine):
        r = eval_harness(engine, action="define", eval_type=EVAL_CAPABILITY,
                         task="Test login flow",
                         criteria=["User can log in", "Session persists"])
        assert r["eval_id"].startswith("EVAL-")
        assert r["type"] == "capability"
        assert len(r["criteria"]) == 2

    def test_run_eval(self, engine):
        r = eval_harness(engine, action="run", eval_id="EVAL-TEST-1",
                         results=[True, True, False, True, True])
        assert r["pass_rate"] == 0.8
        assert r["total_trials"] == 5
        assert "pass@1" in r["pass_at_k"]

    def test_pass_at_k_all_pass(self):
        assert _pass_at_k([True, True, True], 1) == 1.0

    def test_pass_at_k_all_fail(self):
        assert _pass_at_k([False, False, False], 1) == 0.0

    def test_pass_at_k_mixed(self):
        result = _pass_at_k([True, False, True, False], 2)
        assert 0.0 < result < 1.0

    def test_pass_at_k_empty(self):
        assert _pass_at_k([], 1) == 0.0

    def test_run_with_custom_k(self, engine):
        r = eval_harness(engine, action="run", eval_id="EVAL-K",
                         results=[True, True, False],
                         k_values=[1, 2, 3])
        assert "pass@1" in r["pass_at_k"]
        assert "pass@2" in r["pass_at_k"]
        assert "pass@3" in r["pass_at_k"]

    def test_report(self, engine):
        eval_harness(engine, action="define", eval_id="EVAL-R1",
                     task="test", criteria=["c1"])
        eval_harness(engine, action="run", eval_id="EVAL-R1",
                     results=[True, True])
        r = eval_harness(engine, action="report")
        assert r["defined_count"] >= 1
        assert r["completed_count"] >= 1
        assert "aggregate" in r

    def test_define_emits_event(self, engine):
        eval_harness(engine, action="define", task="test")
        events = engine.event_bus.query(type="diagnostic:eval")
        assert len(events) >= 1

    def test_invalid_eval_type_fallback(self, engine):
        r = eval_harness(engine, action="define", eval_type="invalid",
                         task="test")
        assert r["type"] == "capability"

    def test_invalid_action(self, engine):
        r = eval_harness(engine, action="invalid")
        assert "error" in r


# ── rules_distill ────────────────────────────────────────────────────

class TestRulesDistill:

    def test_scan_finds_patterns(self, engine):
        # Create some events to find patterns in
        for _ in range(5):
            engine.event_bus.emit("host:event", "external",
                {"text": "implementing authentication correctly"})
        r = rules_distill(engine, action="scan",
                          source_types=["skills", "events"],
                          min_occurrences=2)
        assert r["total_patterns"] > 0

    def test_scan_with_min_occurrences(self, engine):
        r = rules_distill(engine, action="scan", min_occurrences=100)
        # With high threshold, likely no significant patterns
        assert "significant_patterns" in r

    def test_distill_creates_rule(self, engine):
        r = rules_distill(engine, action="distill",
                          rule_text="Always validate inputs before processing",
                          source_types=["skills"])
        assert r["rule_id"].startswith("RULE-")
        assert r["text"] == "Always validate inputs before processing"

    def test_distill_with_custom_id(self, engine):
        r = rules_distill(engine, action="distill",
                          rule_text="Test rule",
                          rule_id="RULE-CUSTOM-1")
        assert r["rule_id"] == "RULE-CUSTOM-1"

    def test_distill_without_text_errors(self, engine):
        r = rules_distill(engine, action="distill", rule_text="")
        assert "error" in r

    def test_list_rules(self, engine):
        rules_distill(engine, action="distill", rule_text="Rule 1")
        rules_distill(engine, action="distill", rule_text="Rule 2")
        r = rules_distill(engine, action="list")
        assert r["total"] >= 2

    def test_scan_emits_no_event(self, engine):
        # scan is read-only, no event emitted
        events_before = len(engine.event_bus.query(type="diagnostic:rule"))
        rules_distill(engine, action="scan")
        events_after = len(engine.event_bus.query(type="diagnostic:rule"))
        assert events_after == events_before

    def test_distill_emits_event(self, engine):
        rules_distill(engine, action="distill", rule_text="Test rule")
        events = engine.event_bus.query(type="diagnostic:rule")
        assert len(events) >= 1

    def test_scan_event_type_patterns(self, engine):
        # Create some events of known types
        for _ in range(3):
            engine.event_bus.emit("host:event", "external", {"text": "test"})
        r = rules_distill(engine, action="scan",
                          source_types=["events"], min_occurrences=2)
        assert "patterns" in r

    def test_invalid_action(self, engine):
        r = rules_distill(engine, action="invalid")
        assert "error" in r

    def test_closed_engine_raises(self, tmp_path):
        e = ConsciousnessEngine(model_name="t", storage_path=str(tmp_path))
        e.close()
        with pytest.raises(RuntimeError):
            rules_distill(e, action="scan")
