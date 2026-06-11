# tests/test_coherence_engine_integration.py
from conscio.engine import ConsciousnessEngine
from conscio.coherence import CoherenceReport, Dissonance


def _engine(tmp_path, **kw):
    return ConsciousnessEngine(model_name="glm-5.1", storage_path=str(tmp_path), **kw)


def test_reflect_populates_coherence(tmp_path):
    result = _engine(tmp_path).reflect(world_state="bot running", confidence=0.7)
    assert 0.0 <= result["coherence"] <= 1.0
    assert set(result["coherence_dimensions"]) == {
        "epistemic", "reality", "ontological", "temporal"}
    assert "dominant_dissonance" in result


def test_reflect_stores_last_coherence(tmp_path):
    eng = _engine(tmp_path)
    eng.reflect(world_state="x", confidence=0.5)
    assert eng.last_coherence is not None
    assert hasattr(eng.last_coherence, "score")


def test_voice_preset_default_resolves(tmp_path):
    assert _engine(tmp_path).voice_preset == "coherence-style"


def test_voice_preset_none_disables(tmp_path):
    assert _engine(tmp_path, voice_preset="none").voice_preset == ""


def test_low_coherence_emits_dissonance_event(tmp_path):
    eng = _engine(tmp_path)
    low = CoherenceReport(
        score=0.2,
        dimensions={"epistemic": 0.0, "reality": 0.0, "ontological": 1.0, "temporal": 1.0},
        dissonances=[Dissonance("epistemic", 0.0, 1.0, "x")],
        dominant=Dissonance("epistemic", 0.0, 1.0, "x"),
    )
    # Intentional seam: stub assess() to force a sub-threshold score. Driving a
    # real low-coherence state would require corrupting meta/world fixtures;
    # stubbing the pure method is the cleaner, deterministic way to exercise the
    # emission branch. CoherenceEngine purity (no bus coupling) is what makes
    # this swap trivial.
    eng.coherence.assess = lambda recent: low
    eng.reflect(world_state="x", confidence=0.5)
    events = [e.to_dict() for e in eng.event_bus.query(limit=50)]
    assert any(e["type"] == "coherence:dissonance" for e in events)
