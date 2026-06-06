# tests/test_engine_meta_reflect.py
from conscio.engine import ConsciousnessEngine


def _engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    e._session_rag = None
    return e


def test_meta_confidence_bounded_and_capped(tmp_path):
    e = _engine(tmp_path)
    res = e.reflect(world_state="all nominal", confidence=0.8)
    assert 0.0 <= res["meta_confidence"] <= 1.0
    assert res["meta_confidence"] <= 0.8 + 1e-9          # never exceeds input confidence
    assert res["reflection_quality"] in ("HIGH", "MEDIUM", "LOW")
    e.close()


def test_meta_confidence_drops_with_anomalies(tmp_path):
    e = _engine(tmp_path)
    clean = e.reflect(world_state="s", confidence=0.9)["meta_confidence"]
    noisy = e.reflect(world_state="s", confidence=0.9,
                      anomalies=["x", "y", "z"])["meta_confidence"]
    assert noisy < clean
    e.close()


def test_meta_confidence_drops_with_prediction_errors(tmp_path):
    e = _engine(tmp_path)
    base = e.reflect(world_state="s", confidence=0.9)["meta_confidence"]
    e.world.record_prediction("x", "up", "down")          # error_rate -> 1.0
    worse = e.reflect(world_state="s", confidence=0.9)["meta_confidence"]
    assert worse < base
    e.close()


def test_meta_confidence_rides_reflection_event(tmp_path):
    e = _engine(tmp_path)
    e.reflect(world_state="s", confidence=0.7)
    evs = e.event_bus.query(type="reflection", limit=1)
    assert "meta_confidence" in evs[0].data
    e.close()


def test_reflection_quality_in_injection(tmp_path):
    e = _engine(tmp_path)
    e.reflect(world_state="s", confidence=0.9)
    assert "reflection quality" in e.get_state_for_injection().lower()
    e.close()
