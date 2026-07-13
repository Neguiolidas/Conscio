"""Wiring tests: engine.perceive() populates world-model entities in prod.

The MCP server calls `engine.perceive(world_state)` with no entities, so the
world model's entities dict was never populated in production. The wiring
contract: when `entities is None`, perceive derives them deterministically
via `conscio.world_extract.extract_entities`; an explicit dict (including
an explicit empty `{}`) always wins over extraction.
"""
import pytest

from conscio.engine import ConsciousnessEngine


@pytest.fixture
def engine(tmp_path):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    yield eng
    eng.close()


def _wm(eng):
    return eng.content_layer.world_model


def test_perceive_extracts_entities_when_none_given(engine):
    engine.perceive("[host]\nstatus: degraded\nlatency_ms=245.0\nalerting=True")
    wm = _wm(engine)
    status = wm.get_entity("status")
    assert status["state"] == "degraded"
    assert status["type"] == "attribute"
    latency = wm.get_entity("latency_ms")
    assert latency["state"] == "245.0"
    assert latency["type"] == "metric"
    assert wm.get_entity("alerting")["type"] == "flag"


def test_explicit_entities_win_over_extraction(engine):
    engine.perceive("status: degraded",
                    entities={"BTC": {"type": "asset", "state": "bullish"}})
    wm = _wm(engine)
    assert wm.get_entity("BTC") is not None
    # Explicit dict replaces extraction entirely — no merge.
    assert wm.get_entity("status") is None


def test_explicit_empty_dict_suppresses_extraction(engine):
    engine.perceive("status: degraded", entities={})
    assert _wm(engine).list_entities(limit=100) == []


def test_free_text_world_state_extracts_nothing(engine):
    engine.perceive("BTC spiked 2% overnight")
    assert _wm(engine).list_entities(limit=100) == []


def test_reperception_updates_state_in_place(engine):
    engine.perceive("[host]\nstatus: ok")
    engine.perceive("[host]\nstatus: degraded")
    wm = _wm(engine)
    assert wm.get_entity("status")["state"] == "degraded"
    assert len(wm.list_entities(limit=100)) == 1
