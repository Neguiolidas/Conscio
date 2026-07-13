# tests/test_confidence_loop.py
"""#148: close the confidence loop (TDD).

Wiring under test:
  1. WorldModel.recent_prediction_outcomes() — (errors, total) evidence counts,
     disambiguating "no data" from "perfect" (error_rate returns 0.0 for both).
  2. engine._reflect_once resolves the previous pending "general" confidence
     record via meta.update_outcome() when prediction evidence exists.
  3. A resolved *failure* records a self-critique (meta.add_critique) — guarded
     by actual resolution, so no evidence → no critique.
  4. generate_self_prompts surfaces meta.recent_critiques() as prompts.

Hermetic: RAG pinned off, explicit entities (extraction suppressed), no LLM.
"""
from types import SimpleNamespace

import pytest

from conscio.content_layer import _RAG_DISABLED
from conscio.engine import ConsciousnessEngine
from conscio.meta_cognition import MetaCognition
from conscio.self_prompt import generate_self_prompts
from conscio.world_model import WorldModel


@pytest.fixture
def engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    e.content_layer._session_rag = _RAG_DISABLED  # hermetic: no Ollama probe
    return e


def _ent(state: str) -> dict:
    return {"BTC": {"type": "asset", "state": state, "attributes": {}}}


def _general_history(engine) -> list[dict]:
    return [
        e for e in engine.meta._data["confidence_history"]
        if e["task_type"] == "general"
    ]


# --- 1. WorldModel evidence counts ---------------------------------------

def test_recent_prediction_outcomes_counts(tmp_path):
    wm = WorldModel(tmp_path)
    assert wm.recent_prediction_outcomes(window_hours=24) == (0, 0)
    wm.add_entity("BTC", "asset", state="bullish")   # no prior belief → no log
    wm.add_entity("BTC", "asset", state="bullish")   # held → success logged
    assert wm.recent_prediction_outcomes(window_hours=24) == (0, 1)
    wm.add_entity("BTC", "asset", state="bearish")   # flip → error logged
    assert wm.recent_prediction_outcomes(window_hours=24) == (1, 2)


def test_error_rate_consistent_with_outcomes(tmp_path):
    wm = WorldModel(tmp_path)
    wm.add_entity("BTC", "asset", state="bullish")
    wm.add_entity("BTC", "asset", state="bearish")
    errors, total = wm.recent_prediction_outcomes(window_hours=24)
    assert total == 1
    assert wm.recent_prediction_error_rate(window_hours=24) == errors / total


# --- 2. update_outcome wired into reflect ---------------------------------

def test_update_outcome_returns_resolution_flag(tmp_path):
    m = MetaCognition(tmp_path)
    assert m.update_outcome("general", "success") is False  # nothing pending
    m.record_confidence("general", 0.6)
    assert m.update_outcome("general", "success") is True


def test_reflect_resolves_pending_on_success_evidence(engine):
    engine.perceive("btc steady", entities=_ent("bullish"))
    engine.perceive("btc steady", entities=_ent("bullish"))  # success evidence
    engine.reflect(world_state="cycle 1", confidence=0.7)    # creates pending
    engine.reflect(world_state="cycle 2", confidence=0.7)    # resolves it
    hist = _general_history(engine)
    assert hist[-1]["outcome"] == "pending"      # fresh record stays open
    assert hist[-2]["outcome"] == "success"      # previous resolved by evidence


def test_reflect_without_evidence_leaves_pending(engine):
    engine.reflect(world_state="cycle 1", confidence=0.7)
    engine.reflect(world_state="cycle 2", confidence=0.7)
    assert all(e["outcome"] == "pending" for e in _general_history(engine))
    assert engine.meta.recent_critiques() == []


# --- 3. Resolved failure → self-critique -----------------------------------

def test_reflect_failure_evidence_records_critique(engine):
    engine.perceive("btc", entities=_ent("bullish"))
    engine.perceive("btc", entities=_ent("bearish"))  # error → rate 1.0
    engine.reflect(world_state="cycle 1", confidence=0.7)
    engine.reflect(world_state="cycle 2", confidence=0.7)
    hist = _general_history(engine)
    assert hist[-2]["outcome"] == "failure"
    crits = engine.meta.recent_critiques()
    assert crits, "resolved failure should record a self-critique"
    assert crits[-1]["task"] == "prediction"


def test_success_resolution_records_no_critique(engine):
    engine.perceive("btc", entities=_ent("bullish"))
    engine.perceive("btc", entities=_ent("bullish"))
    engine.reflect(world_state="cycle 1", confidence=0.7)
    engine.reflect(world_state="cycle 2", confidence=0.7)
    assert engine.meta.recent_critiques() == []


# --- 4. Critiques surface as self-prompts ----------------------------------

def test_self_prompts_surface_critiques():
    class _Meta:
        def blind_spots(self):
            return []

        def recent_critiques(self, n=5):
            return [{
                "task": "prediction",
                "what_i_did": "held a stale belief",
                "what_i_should_do": "re-perceive volatile entities first",
            }]

    class _World:
        def stale_entities(self, *a, **k):
            return []

    report = SimpleNamespace(dissonances=[])
    prompts = generate_self_prompts(_Meta(), _World(), report)
    crit = [p for p in prompts if p.source_signal == "critique"]
    assert crit, "recent critiques should become self-prompts"
    assert "re-perceive volatile entities first" in crit[0].question
    assert crit[0].drive == "evolution"
