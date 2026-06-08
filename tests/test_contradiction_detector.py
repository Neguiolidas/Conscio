# tests/test_contradiction_detector.py
from conscio.semantic import SemanticEngine, ContradictionDetector


class StubEmbedder:
    _VOCAB = {
        "operational": [1, 0, 0, 0], "offline": [-1, 0, 0, 0],
        "owns": [0, 0, 1, 0], "holds": [0, 0, 1, 0],
        "possesses": [0, 0, 1, 0], "controls": [0, 0, 1, 0],
        "sold": [0, 0, -1, 0], "released": [0, 0, -1, 0],
        "divested": [0, 0, -1, 0], "lost": [0, 0, -1, 0],
    }
    def embed(self, text):
        return [float(x) for x in self._VOCAB.get((text or "").strip().lower(), [0, 1, 0, 0])]
    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


class DeadEmbedder:
    def embed(self, text):
        return []
    def embed_batch(self, texts):
        return [[] for _ in texts]


AVAIL = [{"axis": "availability", "positive": ["operational"], "negative": ["offline"]}]
OWN = [{"axis": "ownership", "positive": ["owns", "holds", "possesses", "controls"],
        "negative": ["sold", "released", "divested", "lost"]}]


def test_lexical_offline_negation_contradicts():
    d = ContradictionDetector(semantic=None)
    assert d.relations_contradict("owns", "not owns") is True


def test_lexical_offline_distinct_predicates_false():
    d = ContradictionDetector(semantic=None)
    assert d.relations_contradict("owns", "sold") is False  # no axis offline


def test_offline_embedder_axis_opposition_false():
    d = ContradictionDetector(SemanticEngine(embedder=DeadEmbedder(), axes=OWN))
    assert d.relations_contradict("owns", "sold") is False


def test_semantic_relation_axis_opposition_true():
    d = ContradictionDetector(SemanticEngine(embedder=StubEmbedder(), axes=OWN))
    assert d.relations_contradict("owns", "sold") is True


def test_semantic_states_axis_opposition_true():
    d = ContradictionDetector(SemanticEngine(embedder=StubEmbedder(), axes=AVAIL))
    assert d.states_contradict("operational", "offline") is True


def test_states_lexical_negation_offline_true():
    d = ContradictionDetector(semantic=None)
    assert d.states_contradict("operational", "not operational") is True


def test_lexical_wins_when_embedder_dead():
    # Ordering invariant: the lexical fast-path must fire BEFORE the availability
    # short-circuit — a present-but-dead embedder still yields TRUE on negation.
    d = ContradictionDetector(SemanticEngine(embedder=DeadEmbedder(), axes=OWN))
    assert d.relations_contradict("owns", "not owns") is True


def test_available_embedder_neutral_pair_false():
    # Live embedder, non-opposing terms (different axes) → FALSE; guards against
    # an opposes() that wrongly reports opposition.
    d = ContradictionDetector(SemanticEngine(embedder=StubEmbedder(), axes=OWN))
    assert d.relations_contradict("owns", "operational") is False
