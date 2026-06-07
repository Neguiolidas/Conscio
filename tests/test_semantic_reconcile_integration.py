# tests/test_semantic_reconcile_integration.py
from conscio.engine import ConsciousnessEngine
from conscio.semantic import SemanticEngine, ContradictionDetector
from conscio.coherence import CoherenceReport, Dissonance


class StubEmbedder:
    _VOCAB = {
        "owns": [0, 0, 1, 0], "holds": [0, 0, 1, 0],
        "possesses": [0, 0, 1, 0], "controls": [0, 0, 1, 0],
        "sold": [0, 0, -1, 0], "released": [0, 0, -1, 0],
        "divested": [0, 0, -1, 0], "lost": [0, 0, -1, 0],
    }
    def embed(self, text):
        return [float(x) for x in self._VOCAB.get((text or "").strip().lower(), [0, 1, 0, 0])]
    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


OWN = [{"axis": "ownership", "positive": ["owns", "holds", "possesses", "controls"],
        "negative": ["sold", "released", "divested", "lost"]}]


def _semantic_engine(eng):
    """Swap in a deterministic stub-backed detector (no Ollama)."""
    sem = SemanticEngine(embedder=StubEmbedder(), axes=OWN)
    eng._contradiction_detector = ContradictionDetector(sem)


def test_dream_reconcile_flags_semantic_contradiction(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    _semantic_engine(eng)
    eng.world.add_entity("acme", "company")
    eng.world.add_entity("unit", "asset")
    eng.world.add_relation("acme", "owns", "unit")
    eng.world.add_relation("acme", "sold", "unit")   # NOT lexical — semantic only
    rep = eng.dream()
    assert "acme" in rep.reconciled_entities
    eng.close()


def test_ontological_score_improves_after_resolution(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    _semantic_engine(eng)
    eng.world.add_entity("acme", "company")
    eng.world.add_entity("unit", "asset")
    eng.world.add_relation("acme", "owns", "unit")
    eng.world.add_relation("acme", "sold", "unit")
    # Mark contradictions, then score: 1 of 2 entities contradicted → 0.5.
    eng.world.mark_contradictions(eng._contradiction_detector)
    from conscio.coherence import ontological_score
    before = ontological_score(eng.world)
    assert before == 0.5
    # Resolve by removing the contradicted entity; re-mark; score recovers.
    eng.world.remove_entity("acme")
    eng.world.mark_contradictions(eng._contradiction_detector)
    after = ontological_score(eng.world)
    assert after > before
    eng.close()
