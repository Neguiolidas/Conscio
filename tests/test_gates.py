"""Tests for gates module — decide, council, loop_gate, delivery_check, investigate."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from conscio import ConsciousnessEngine
from conscio.gates import (
    ADR_VALID_STATUSES,
    COUNCIL_ROLES,
    COUNCIL_VOTES,
    decide,
    council,
    loop_gate,
    delivery_check,
    investigate,
)


@pytest.fixture
def engine(tmp_path):
    with ConsciousnessEngine(model_name="test", storage_path=str(tmp_path)) as e:
        yield e


# ═══════════════════════════════════════════════════════════════════════
# Task 1a: VALID_TYPES
# ═══════════════════════════════════════════════════════════════════════

class TestValidTypes:
    def test_adr_proposed_accepted(self, engine):
        eid = engine.event_bus.emit("adr:proposed", "consciousness", {"title": "test"})
        assert eid > 0
        eid2 = engine.event_bus.emit("adr:accepted", "consciousness", {"id": "ADR-001"})
        assert eid2 > 0

    def test_council_convened(self, engine):
        eid = engine.event_bus.emit("council:convened", "consciousness", {"q": "test"})
        assert eid > 0

    def test_gate_vetoed(self, engine):
        eid = engine.event_bus.emit("gate:vetoed", "consciousness", {"gate": "loop"})
        assert eid > 0


# ═══════════════════════════════════════════════════════════════════════
# Task 1b: decide()
# ═══════════════════════════════════════════════════════════════════════

class TestDecide:
    def test_decide_returns_adr_id(self, engine):
        result = decide(engine, title="Use SQLite for storage",
                        context="Need embedded storage, no external deps",
                        alternatives=["SQLite", "PostgreSQL", "Files"])
        assert "adr_id" in result
        assert result["status"] == "proposed"
        assert result["title"] == "Use SQLite for storage"
        assert result["adr_id"].startswith("ADR-")

    def test_decide_emits_event(self, engine):
        decide(engine, title="test", context="ctx", alternatives=["a", "b"])
        events = engine.event_bus.query(type="adr:proposed")
        assert len(events) == 1
        assert events[0].data["title"] == "test"

    def test_decide_status_transition(self, engine):
        result = decide(engine, title="test", context="ctx")
        adr_id = result["adr_id"]
        updated = decide(engine, adr_id=adr_id, status="accepted")
        assert updated["status"] == "accepted"
        events = engine.event_bus.query(type="adr:accepted")
        assert len(events) == 1

    def test_decide_invalid_status_raises(self, engine):
        with pytest.raises(ValueError, match="Invalid ADR status"):
            decide(engine, title="x", status="invalid")

    def test_decide_no_title_raises(self, engine):
        with pytest.raises(ValueError, match="title is required"):
            decide(engine, context="ctx")

    def test_decide_update_nonexistent(self, engine):
        result = decide(engine, adr_id="ADR-NONEXISTENT", status="accepted")
        assert "error" in result

    def test_decide_superseded(self, engine):
        result = decide(engine, title="old decision", context="c")
        adr_id = result["adr_id"]
        updated = decide(engine, adr_id=adr_id, status="superseded")
        assert updated["status"] == "superseded"

    def test_decide_deprecated(self, engine):
        result = decide(engine, title="deprecated decision", context="c")
        adr_id = result["adr_id"]
        updated = decide(engine, adr_id=adr_id, status="deprecated")
        assert updated["status"] == "deprecated"

    def test_decide_with_deciders(self, engine):
        result = decide(engine, title="team decision", context="c",
                        deciders=["alice", "bob"])
        assert result["deciders"] == ["alice", "bob"]


# ═══════════════════════════════════════════════════════════════════════
# Task 2: council()
# ═══════════════════════════════════════════════════════════════════════

class TestCouncil:
    def test_council_returns_4_voices(self, engine):
        result = council(engine, question="Should we use SQLite?", context="Need storage")
        assert len(result["voices"]) == 4
        roles = {v["role"] for v in result["voices"]}
        assert roles == {"architect", "skeptic", "pragmatist", "critic"}

    def test_each_voice_has_analysis_and_vote(self, engine):
        result = council(engine, question="test question")
        for v in result["voices"]:
            assert "analysis" in v
            assert "vote" in v
            assert v["vote"] in COUNCIL_VOTES

    def test_council_emits_event(self, engine):
        council(engine, question="test")
        events = engine.event_bus.query(type="council:convened")
        assert len(events) == 1

    def test_council_has_recommendation(self, engine):
        result = council(engine, question="test")
        assert "recommendation" in result
        assert result["recommendation"] in COUNCIL_VOTES

    def test_council_votes_summary(self, engine):
        result = council(engine, question="test")
        assert "votes_summary" in result
        total = sum(result["votes_summary"].values())
        assert total == 4

    def test_council_no_question_raises(self, engine):
        with pytest.raises(ValueError, match="question is required"):
            council(engine, question="")

    def test_council_voices_have_concerns(self, engine):
        result = council(engine, question="test")
        for v in result["voices"]:
            assert "concerns" in v
            assert isinstance(v["concerns"], list)

    def test_critic_deterministic_fallback(self, engine):
        # No adapter attached — critic should fall back to deterministic
        result = council(engine, question="Should we delete prod DB?")
        critic = [v for v in result["voices"] if v["role"] == "critic"][0]
        assert "analysis" in critic
        assert len(critic["analysis"]) > 0


# ═══════════════════════════════════════════════════════════════════════
# Task 3: loop_gate()
# ═══════════════════════════════════════════════════════════════════════

class TestLoopGate:
    def test_all_pass_approves(self, engine):
        result = loop_gate(engine, task="daily backup", frequency="daily",
                           verifiable=True, budget_ok=True, has_tools=True)
        assert result["approved"] is True
        assert result["vetoed_conditions"] == []

    def test_any_fail_vetoes(self, engine):
        result = loop_gate(engine, task="rare task", frequency="monthly",
                           verifiable=True, budget_ok=True, has_tools=False)
        assert result["approved"] is False
        assert "has_tools" in result["vetoed_conditions"]

    def test_veto_emits_event(self, engine):
        loop_gate(engine, task="test", frequency="monthly",
                  verifiable=False, budget_ok=True, has_tools=True)
        events = engine.event_bus.query(type="gate:vetoed")
        assert len(events) == 1

    def test_no_veto_no_event(self, engine):
        loop_gate(engine, task="test", frequency="daily",
                  verifiable=True, budget_ok=True, has_tools=True)
        events = engine.event_bus.query(type="gate:vetoed")
        assert len(events) == 0

    def test_empty_frequency_vetoes(self, engine):
        result = loop_gate(engine, task="test", frequency="",
                           verifiable=True, budget_ok=True, has_tools=True)
        assert result["approved"] is False
        assert "frequency" in result["vetoed_conditions"]

    def test_multiple_failures(self, engine):
        result = loop_gate(engine, task="test", frequency="",
                           verifiable=False, budget_ok=False, has_tools=False)
        assert result["approved"] is False
        assert len(result["vetoed_conditions"]) == 4

    def test_conditions_dict(self, engine):
        result = loop_gate(engine, task="test", frequency="daily",
                           verifiable=True, budget_ok=True, has_tools=True)
        assert "conditions" in result
        assert all(k in result["conditions"] for k in ["frequency", "verifiable", "budget_ok", "has_tools"])


# ═══════════════════════════════════════════════════════════════════════
# Task 4: delivery_check()
# ═══════════════════════════════════════════════════════════════════════

class TestDeliveryCheck:
    def test_clean_engine_passes(self, engine):
        result = delivery_check(engine)
        assert result["pass"] is True
        assert len(result["blockers"]) == 0

    def test_detects_rationalization(self, engine):
        engine.event_bus.emit("host:event", "external",
            {"text": "it probably works, let's just ship it"})
        engine.event_bus.emit("host:event", "external",
            {"text": "edge cases are unlikely, skip for now"})
        result = delivery_check(engine)
        assert result["rationalization_hits"] >= 2

    def test_rationalization_blocks(self, engine):
        engine.event_bus.emit("host:event", "external",
            {"text": "it probably works, let's just ship it"})
        engine.event_bus.emit("host:event", "external",
            {"text": "edge cases are unlikely, skip for now"})
        result = delivery_check(engine)
        assert result["pass"] is False

    def test_low_disk_blocks(self, engine, monkeypatch):
        import conscio.gates as gates_mod
        monkeypatch.setattr(gates_mod.shutil, "disk_usage",
                            lambda p: type("", (), {"free": 0, "total": 1, "used": 1}))
        result = delivery_check(engine)
        assert result["pass"] is False

    def test_delivery_check_emits_system_event(self, engine):
        delivery_check(engine)
        events = engine.event_bus.query(type="system")
        delivery_events = [e for e in events if e.data.get("check") == "delivery"]
        assert len(delivery_events) >= 1

    def test_auto_runs_on_close(self, tmp_path):
        eng = ConsciousnessEngine(model_name="t", storage_path=str(tmp_path))
        # delivery_check runs during close() BEFORE SQLite is closed
        eng.close()
        # Re-open to check (EventBus uses shared conscio.db)
        from conscio.event_bus import EventBus
        eb = EventBus(str(tmp_path / "conscio.db"))
        events = eb.query(type="system")
        delivery_events = [e for e in events if e.data.get("check") == "delivery"]
        eb.close()
        assert len(delivery_events) >= 1

    def test_auto_runs_disabled(self, tmp_path):
        eng = ConsciousnessEngine(model_name="t", storage_path=str(tmp_path),
                                   delivery_check=False)
        eng.close()
        from conscio.event_bus import EventBus
        eb = EventBus(str(tmp_path / "conscio.db"))
        events = eb.query(type="system")
        delivery_events = [e for e in events if e.data.get("check") == "delivery"]
        eb.close()
        assert len(delivery_events) == 0


# ═══════════════════════════════════════════════════════════════════════
# Task 5: investigate()
# ═══════════════════════════════════════════════════════════════════════

class TestInvestigate:
    def test_blocks_without_reads(self, engine):
        result = investigate(engine, target="config.py", action_type="edit")
        assert result["satisfied"] is False
        assert len(result["missing"]) > 0

    def test_passes_after_read_note(self, engine):
        engine.event_bus.emit("host:event", "external",
            {"investigate:read": "config.py", "text": "read config.py contents"})
        result = investigate(engine, target="config.py", action_type="edit")
        assert result["satisfied"] is True

    def test_passes_after_host_event(self, engine):
        engine.event_bus.emit("host:event", "external",
            {"investigate:read": "config.py", "source": "grep"})
        result = investigate(engine, target="config.py", action_type="edit")
        assert result["satisfied"] is True

    def test_veto_emits_event(self, engine):
        investigate(engine, target="x.py", action_type="edit")
        events = engine.event_bus.query(type="gate:vetoed")
        assert len(events) == 1

    def test_no_target_raises(self, engine):
        with pytest.raises(ValueError, match="target is required"):
            investigate(engine, target="", action_type="edit")

    def test_suffix_match(self, engine):
        engine.event_bus.emit("host:event", "external",
            {"investigate:read": "config", "text": "read config"})
        result = investigate(engine, target="config.py", action_type="edit")
        assert result["satisfied"] is True

    def test_no_veto_when_satisfied(self, engine):
        engine.event_bus.emit("host:event", "external",
            {"investigate:read": "app.py", "text": "read app.py"})
        investigate(engine, target="app.py", action_type="edit")
        events = engine.event_bus.query(type="gate:vetoed")
        assert len(events) == 0
