# tests/test_recursive_loop_integration.py
from conscio.engine import ConsciousnessEngine
from conscio.coherence import CoherenceReport, Dissonance


def _low_ontological():
    d = Dissonance("ontological", 0.1, 0.9, "x")
    return CoherenceReport(0.30, {"epistemic": 0.5, "reality": 1.0,
                                  "ontological": 0.1, "temporal": 1.0}, [d], d)


def test_low_coherence_sets_flag_and_spawns_goal(tmp_path, monkeypatch):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    # Registered test seam: stub the PURE assess() to force low coherence.
    monkeypatch.setattr(eng.coherence, "assess", lambda recent=None: _low_ontological())
    eng.reflect(world_state="test", confidence=0.5)
    assert eng.dream_recommended.recommended is True
    assert eng.dream_recommended.dominant == "ontological"
    sp_goals = [g for g in eng.goals._goals if g.source == "self_prompt"]
    assert len(sp_goals) >= 1
    eng.close()


def test_dream_clears_flag(tmp_path, monkeypatch):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    monkeypatch.setattr(eng.coherence, "assess", lambda recent=None: _low_ontological())
    eng.reflect(world_state="test", confidence=0.5)
    assert eng.dream_recommended.recommended is True
    eng.dream()
    assert eng.dream_recommended.recommended is False
    eng.close()


def test_healthy_coherence_no_flag(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    # Fresh engine: no prediction errors, no entities → coherence high.
    res = eng.reflect(world_state="all nominal", confidence=0.8)
    assert eng.dream_recommended.recommended is False
    assert "self_prompts" in res and "dream_recommended" in res
    eng.close()
