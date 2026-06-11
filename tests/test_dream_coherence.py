# tests/test_dream_coherence.py
from conscio.engine import ConsciousnessEngine
from conscio.coherence import CoherenceReport, Dissonance


def _ontological_low():
    # Intentional seam: tests below set `eng.last_coherence` directly instead of
    # going through reflect(). This is the SAME object reflect() caches
    # (engine.py `self.last_coherence = coherence_report`) — a real
    # CoherenceReport whose `.dominant` is a real Dissonance with `.dimension`.
    # dream() reads `last_coherence.dominant.dimension`, so this hand-built report
    # exercises the exact field path the live reflect→dream loop uses.
    d = Dissonance("ontological", 0.1, 0.9, "x")
    return CoherenceReport(0.30, {}, [d], d)


def test_dream_records_coherence_delta(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    rep = eng.dream()
    assert rep.coherence_before is not None
    assert rep.coherence_after is not None
    assert "coherence_before" in rep.to_dict()
    eng.close()


def test_ontological_targeting_prunes_contradictions(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    eng.world.add_entity("gateway", "system")
    eng.world.add_entity("svc", "system")
    eng.world.add_relation("gateway", "owns", "svc")
    eng.world.add_relation("gateway", "not owns", "svc")
    eng.last_coherence = _ontological_low()
    rep = eng.dream()
    assert "gateway" in rep.contradictions_pruned
    assert eng.world.get_entity("gateway") is None
    eng.close()


def test_dry_run_no_mutation(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    eng.world.add_entity("gateway", "system")
    eng.world.add_entity("svc", "system")
    eng.world.add_relation("gateway", "owns", "svc")
    eng.world.add_relation("gateway", "not owns", "svc")
    eng.last_coherence = _ontological_low()
    rep = eng.dream(dry_run=True)
    assert "gateway" in rep.contradictions_pruned          # would-prune
    assert eng.world.get_entity("gateway") is not None     # but not actually pruned
    eng.close()


def test_no_targeting_when_dominant_not_ontological(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    eng.world.add_entity("gateway", "system")
    eng.world.add_relation("gateway", "owns", "svc")
    eng.world.add_relation("gateway", "not owns", "svc")
    d = Dissonance("epistemic", 0.1, 0.9, "x")
    eng.last_coherence = CoherenceReport(0.30, {}, [d], d)
    rep = eng.dream()
    assert rep.contradictions_pruned == []
    eng.close()
