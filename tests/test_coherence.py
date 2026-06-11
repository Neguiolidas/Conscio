# tests/test_coherence.py
from conscio.coherence import (
    CoherenceEngine, epistemic_score, reality_score, ontological_score, temporal_score,
    _relations_contradict, _strip_neg, _clamp,
    _WEIGHTS, TEMPORAL_FREE_TRANSITIONS,
)


class FakeMeta:
    def __init__(self, cal):
        self._cal = cal
    def calibration_score(self):
        return self._cal


class FakeWorld:
    def __init__(self, err=0.0, entities=None, relations=None, contradicted=None):
        self._err = err
        self._data = {"entities": entities or {}, "relations": relations or []}
        self._contradicted = contradicted or []
    def recent_prediction_error_rate(self, window_hours=24):
        return self._err
    def entity_count(self):
        return len(self._data["entities"])
    def contradicted_entities(self):
        return list(self._contradicted)


def _evt(transition=False):
    return {"type": "system", "data": {"shard_transition": True} if transition else {}}


def test_strip_neg_english():
    core, had = _strip_neg("is not bullish")
    assert core == "is bullish" and had is True


def test_strip_neg_portuguese():
    core, had = _strip_neg("não é estável")
    assert core == "é estável" and had is True


def test_relations_contradict_true():
    assert _relations_contradict("is bullish", "is not bullish") is True


def test_relations_contradict_portuguese():
    assert _relations_contradict("é estável", "não é estável") is True


def test_relations_contradict_distinct_predicates():
    assert _relations_contradict("is bullish", "is bearish") is False


def test_relations_contradict_identical():
    assert _relations_contradict("is up", "is up") is False


def test_relations_contradict_empty_core_guard():
    # two bare negations must not match on an empty core
    assert _relations_contradict("não", "sem") is False


def test_strip_neg_pure_negation_empty_core():
    # a predicate that is ONLY a negation token strips to an empty core
    core, had = _strip_neg("não")
    assert core == "" and had is True


def test_relations_contradict_empty_core_guard_direct():
    # one predicate strips to empty core → the bool(c1) guard returns False
    # even though exactly one side is negated and cores are "equal" ("" == "")
    assert _relations_contradict("não", "") is False


def test_clamp_below_range():
    assert _clamp(-0.5) == 0.0


def test_clamp_above_range():
    assert _clamp(1.7) == 1.0


def test_clamp_within_range():
    assert _clamp(0.42) == 0.42


def test_epistemic_passthrough():
    assert epistemic_score(FakeMeta(0.8)) == 0.8


def test_reality_complement():
    assert reality_score(FakeWorld(err=0.25)) == 0.75


def test_reality_no_log_is_one():
    assert reality_score(FakeWorld(err=0.0)) == 1.0


def test_ontological_no_entities_is_one():
    assert ontological_score(FakeWorld(entities={}, relations=[])) == 1.0


def test_ontological_contradiction_lowers_score():
    # v0.8: ontological_score reads cached `contradicted` flags (detection moved
    # to dream Reconcile). One of two entities flagged → 0.5.
    world = FakeWorld(entities={"market": {}, "btc": {}}, contradicted=["market"])
    assert ontological_score(world) == 0.5


def test_temporal_free_transitions_no_penalty():
    events = [_evt(transition=True) for _ in range(TEMPORAL_FREE_TRANSITIONS)]
    assert temporal_score(events) == 1.0


def test_temporal_flapping_lowers_score():
    # 4 transitions: excess = 4 - FREE(2) = 2; score = 1 - 2/SPAN(4) = 0.5
    events = [_evt(transition=True) for _ in range(4)]
    assert temporal_score(events) == 0.5


def test_temporal_severe_flapping_floor():
    events = [_evt(transition=True) for _ in range(10)]
    assert temporal_score(events) == 0.0


def test_assess_cold_start_healthy():
    rep = CoherenceEngine(FakeMeta(0.5), FakeWorld()).assess([])
    assert rep.score == 0.85          # 0.3*0.5 + 0.3 + 0.2 + 0.2
    assert rep.dominant is None
    assert rep.dissonances == []


def test_assess_bounded():
    # worst-case inputs (cal 0, error 1.0, 10 transitions) stay in-range.
    # _clamp itself is unit-tested directly below; this guards the aggregate.
    rep = CoherenceEngine(FakeMeta(0.0), FakeWorld(err=1.0)).assess([_evt(True)] * 10)
    assert 0.0 <= rep.score <= 1.0


def test_assess_dominant_is_worst_dimension():
    rep = CoherenceEngine(FakeMeta(0.0), FakeWorld()).assess([])
    assert rep.dominant is not None
    assert rep.dominant.dimension == "epistemic"
    assert rep.dominant.severity == 1.0


def test_marker_healthy_no_dominant():
    rep = CoherenceEngine(FakeMeta(0.5), FakeWorld()).assess([])
    assert rep.marker() == "0.85"


def test_marker_with_dominant():
    rep = CoherenceEngine(FakeMeta(0.0), FakeWorld()).assess([])
    assert rep.marker() == f"{rep.score:.2f} dominant: epistemic"


def test_weights_sum_to_one():
    assert round(sum(_WEIGHTS.values()), 6) == 1.0
