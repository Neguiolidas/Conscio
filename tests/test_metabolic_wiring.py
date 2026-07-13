# tests/test_metabolic_wiring.py
"""#147: wire metabolic gates into the reflection cycle.

MetabolicContext.should_dream/should_mitosis were pure advisory predicates with
zero callers — the v0.9 metabolic block computed a human-readable note but no
machine-consumable signal. These tests pin the wiring:

  - CRITICAL context pressure => dream_recommended True (metabolic trigger,
    independent of the coherence-driven v0.7 path).
  - FATIGUE or CRITICAL      => handoff_recommended True (Mitosis advisory).
  - VITAL                    => handoff_recommended False.

Hermetic: no LLM, no Ollama (RAG disabled), token pressure injected via
engine.session_tokens_used.
"""
from conscio.engine import ConsciousnessEngine
from conscio.content_layer import _RAG_DISABLED


def _engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    e.content_layer._session_rag = _RAG_DISABLED   # hermetic: no Ollama probe
    return e


def _reflect_at_pressure(e, fraction):
    window = e.model_info.context_window
    e.session_tokens_used = int(window * fraction)
    return e.reflect(world_state="all nominal", confidence=0.8)


def test_critical_pressure_recommends_dream(tmp_path):
    e = _engine(tmp_path)
    res = _reflect_at_pressure(e, 0.75)            # CRITICAL (>= 70%)
    assert res["dream_recommended"] is True
    e.close()


def test_fatigue_pressure_recommends_handoff(tmp_path):
    e = _engine(tmp_path)
    res = _reflect_at_pressure(e, 0.60)            # FATIGUE (50-70%)
    assert res["handoff_recommended"] is True
    e.close()


def test_critical_pressure_recommends_handoff(tmp_path):
    e = _engine(tmp_path)
    res = _reflect_at_pressure(e, 0.75)            # CRITICAL
    assert res["handoff_recommended"] is True
    e.close()


def test_vital_pressure_recommends_nothing(tmp_path):
    e = _engine(tmp_path)
    res = _reflect_at_pressure(e, 0.10)            # VITAL (< 40%)
    assert res["handoff_recommended"] is False
    e.close()
