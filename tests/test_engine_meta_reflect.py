# tests/test_engine_meta_reflect.py
from conscio.engine import ConsciousnessEngine
from conscio.content_layer import _RAG_DISABLED


def _engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    # Disable RAG to avoid Ollama probes in tests
    e.content_layer._session_rag = _RAG_DISABLED
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


def test_propose_evolution(tmp_path):
    """propose_evolution creates proposals for known types."""
    e = _engine(tmp_path)

    # Skill patch proposal
    prop = e.propose_evolution("skill_patch",
                               skill_name="test_skill",
                               issue="bug in code",
                               suggested_fix="fix the bug",
                               rationale="improves reliability")
    assert prop.get("type") == "skill_patch"
    assert prop.get("status") == "pending"

    # Skill create proposal
    prop = e.propose_evolution("skill_create",
                               skill_name="new_skill",
                               description="does something",
                               content_sketch="def run():\n    pass",
                               rationale="adds new capability")
    assert prop.get("type") == "skill_create"
    assert prop.get("status") == "pending"

    # Memory update proposal
    prop = e.propose_evolution("memory_update",
                               key="test_key",
                               value="new_value",
                               rationale="update data")
    assert prop.get("type") == "memory_update"
    assert prop.get("status") == "pending"

    # Pattern learn proposal
    prop = e.propose_evolution("pattern_learn",
                               pattern="recurring error",
                               lesson="handle it gracefully",
                               rationale="prevents future issues")
    assert prop.get("type") == "pattern_learn"
    assert prop.get("status") == "pending"

    # Unknown type returns error
    prop = e.propose_evolution("unknown_type")
    assert "error" in prop
    assert "Unknown evolution type" in prop["error"]

    e.close()
