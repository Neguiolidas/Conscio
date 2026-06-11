# tests/test_dream_reconcile.py
from conscio.engine import ConsciousnessEngine
from conscio.coherence import CoherenceReport, Dissonance


def _ontological_low():
    d = Dissonance("ontological", 0.1, 0.9, "x")
    return CoherenceReport(0.30, {}, [d], d)


def test_reconcile_populates_reconciled_entities(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    eng.world.add_entity("gw", "system")
    eng.world.add_entity("svc", "system")
    eng.world.add_relation("gw", "owns", "svc")
    eng.world.add_relation("gw", "not owns", "svc")  # lexical contradiction (offline)
    rep = eng.dream()
    assert "gw" in rep.reconciled_entities
    assert "reconciled_entities" in rep.to_dict()
    eng.close()


def test_reconcile_writes_flags_for_hot_path(tmp_path):
    # After a real dream, the cached flag is readable by coherence (no network).
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    eng.world.add_entity("gw", "system")
    eng.world.add_entity("svc", "system")
    eng.world.add_relation("gw", "owns", "svc")
    eng.world.add_relation("gw", "not owns", "svc")
    # dominant NOT ontological → Reconcile still marks, but no aggressive prune.
    d = Dissonance("epistemic", 0.1, 0.9, "x")
    eng.last_coherence = CoherenceReport(0.30, {}, [d], d)
    eng.dream()
    assert "gw" in eng.world.contradicted_entities()   # flag persisted
    assert eng.world.get_entity("gw") is not None       # not pruned (not ontological)
    eng.close()


def test_dry_run_reconcile_does_not_write(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    eng.world.add_entity("gw", "system")
    eng.world.add_entity("svc", "system")
    eng.world.add_relation("gw", "owns", "svc")
    eng.world.add_relation("gw", "not owns", "svc")
    eng.last_coherence = _ontological_low()
    rep = eng.dream(dry_run=True)
    assert "gw" in rep.reconciled_entities          # would-reconcile
    assert eng.world.contradicted_entities() == []  # nothing persisted
    assert eng.world.get_entity("gw") is not None   # nothing pruned
    # A dry dream mutates nothing → before/after assess the same snapshot.
    assert rep.coherence_after == rep.coherence_before
    eng.close()
