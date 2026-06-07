# tests/test_semantic_engine.py
from conscio.semantic import SemanticEngine, AXIS_THRESHOLD, AXIS_MARGIN


class StubEmbedder:
    _VOCAB = {
        "operational": [1, 0, 0, 0], "online": [1, 0, 0, 0], "up": [1, 0, 0, 0],
        "running": [1, 0, 0, 0], "healthy": [1, 0, 0, 0],
        "offline": [-1, 0, 0, 0], "down": [-1, 0, 0, 0], "crashed": [-1, 0, 0, 0],
        "failed": [-1, 0, 0, 0], "unreachable": [-1, 0, 0, 0],
        "degraded": [0, 1, 0, 0],
        "owns": [0, 0, 1, 0], "holds": [0, 0, 1, 0],
        "possesses": [0, 0, 1, 0], "controls": [0, 0, 1, 0],
        "sold": [0, 0, -1, 0], "released": [0, 0, -1, 0],
        "divested": [0, 0, -1, 0], "lost": [0, 0, -1, 0],
        "success": [0, 0, 0, 1], "passed": [0, 0, 0, 1],
        "resolved": [0, 0, 0, 1], "completed": [0, 0, 0, 1],
        "failure": [0, 0, 0, -1], "broken": [0, 0, 0, -1], "unresolved": [0, 0, 0, -1],
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


AVAIL = [{"axis": "availability",
          "positive": ["operational", "online", "up", "running", "healthy"],
          "negative": ["offline", "down", "crashed", "failed", "unreachable"]}]


def _eng():
    return SemanticEngine(embedder=StubEmbedder(), axes=AVAIL)


def test_cosine_identical_is_one():
    assert _eng().cosine("operational", "online") == 1.0


def test_cosine_opposite_is_negative():
    assert _eng().cosine("operational", "offline") < -0.99


def test_available_true_with_stub():
    assert _eng().available() is True


def test_opposes_cross_pole_true():
    assert _eng().opposes("operational", "offline") is True


def test_opposes_same_pole_false():
    assert _eng().opposes("operational", "running") is False


def test_opposes_neutral_term_false():
    # degraded is orthogonal — clears NEITHER pole (threshold+margin guard) →
    # treated as neutral, NOT a contradiction with operational.
    assert _eng().opposes("operational", "degraded") is False


def test_opposes_unrelated_axis_false():
    assert _eng().opposes("operational", "owns") is False


def test_unavailable_embedder_opposes_false():
    eng = SemanticEngine(embedder=DeadEmbedder(), axes=AVAIL)
    assert eng.available() is False
    assert eng.opposes("operational", "offline") is False


class _OrthoPoleEmbedder:
    """Poles are ORTHOGONAL (not antipodal), so the AXIS_MARGIN guard does real
    work: a term equidistant from both poles clears the threshold on EACH yet
    lacks the margin lead → neutral. This is the spec's headline precision case
    (the operational/degraded false-positive the margin prevents)."""
    _V = {"pos": [1.0, 0.0], "neg": [0.0, 1.0],
          "mid": [1.0, 1.0], "near_pos": [1.0, 0.1]}
    def embed(self, t):
        return list(self._V.get((t or "").strip().lower(), [0.0, 0.0]))
    def embed_batch(self, ts):
        return [self.embed(t) for t in ts]


def test_margin_guard_treats_near_equidistant_as_neutral():
    axes = [{"axis": "a", "positive": ["pos"], "negative": ["neg"]}]
    eng = SemanticEngine(embedder=_OrthoPoleEmbedder(), axes=axes)
    # 'mid' = [1,1]: cos to each pole = 0.707 >= AXIS_THRESHOLD, but the lead
    # (0.0) < AXIS_MARGIN → projects to NO pole → neutral, never a contradiction.
    assert eng.opposes("mid", "neg") is False
    # 'near_pos' clears pos with a wide margin; 'neg' is on neg → opposite → True.
    assert eng.opposes("near_pos", "neg") is True
    assert AXIS_MARGIN > 0.0 and AXIS_THRESHOLD < 0.71  # guard the constants used above
