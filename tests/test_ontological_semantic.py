# tests/test_ontological_semantic.py
from conscio.coherence import ontological_score


class CachedWorld:
    """Minimal world exposing ONLY the public accessors ontological_score now
    uses — proves no `_data` access remains."""
    def __init__(self, total, contradicted):
        self._total = total
        self._contradicted = contradicted
    def entity_count(self):
        return self._total
    def contradicted_entities(self):
        return list(self._contradicted)


def test_score_reads_cached_flags():
    assert ontological_score(CachedWorld(2, ["market"])) == 0.5


def test_cold_world_no_flags_is_one():
    # Contradictions may exist in relations, but nothing dreamed yet → 1.0.
    assert ontological_score(CachedWorld(3, [])) == 1.0


def test_no_entities_is_one():
    assert ontological_score(CachedWorld(0, [])) == 1.0


def test_works_without_private_data_attr():
    w = CachedWorld(4, ["a", "b"])
    assert not hasattr(w, "_data")
    assert ontological_score(w) == 0.5
