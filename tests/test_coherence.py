# tests/test_coherence.py
from conscio.coherence import (
    _WEIGHTS,
    TEMPORAL_FREE_TRANSITIONS,
    CoherenceEngine,
    CoherenceReport,
    _clamp,
    _relations_contradict,
    _strip_neg,
    epistemic_score,
    ontological_score,
    reality_score,
    temporal_score,
)
from conscio.meta_cognition import MetaCognition
from conscio.world_model import WorldModel


class FakeMeta:
    def __init__(self, cal, samples=10):
        self._cal = cal
        self._samples = samples
    def calibration_score(self):
        return self._cal
    def has_calibration_evidence(self):
        return self._samples >= 5


class FakeWorld:
    def __init__(self, err=0.0, entities=None, relations=None, contradicted=None,
                 predictions=10):
        self._err = err
        self._data = {"entities": entities or {}, "relations": relations or []}
        self._contradicted = contradicted or []
        self._predictions = predictions
    def recent_prediction_error_rate(self, window_hours=24):
        return self._err
    def recent_prediction_outcomes(self, window_hours=24):
        return round(self._err * self._predictions), self._predictions
    def entity_count(self):
        return len(self._data["entities"])
    def contradicted_entities(self):
        return list(self._contradicted)


def _measured_world(**kw):
    """A world with evidence in every dimension it owns — nothing unmeasured."""
    kw.setdefault("entities", {"e1": {}})
    return FakeWorld(**kw)


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
    rep = CoherenceEngine(FakeMeta(0.5), _measured_world()).assess([_evt()])
    assert rep.unmeasured == ()
    assert rep.marker() == "0.85"


def test_marker_with_dominant():
    rep = CoherenceEngine(FakeMeta(0.0), _measured_world()).assess([_evt()])
    assert rep.marker() == f"{rep.score:.2f} dominant: epistemic"


def test_weights_sum_to_one():
    assert round(sum(_WEIGHTS.values()), 6) == 1.0


# --- v3.9.4: `unmeasured` — telling "clean" apart from "untested" ------------
#
# Real MetaCognition + real WorldModel on purpose: the whole complaint is about
# what a freshly installed mind reports, and a hand-written double cannot prove
# anything about that.

class TestUnmeasuredDimensions:

    def _fresh(self, tmp_path):
        return CoherenceEngine(MetaCognition(storage_path=tmp_path),
                               WorldModel(tmp_path / "wm"))

    def test_fresh_mind_reports_every_dimension_unmeasured(self, tmp_path):
        rep = self._fresh(tmp_path).assess([])
        assert rep.unmeasured == ("epistemic", "reality", "ontological", "temporal")

    def test_the_score_itself_is_untouched(self, tmp_path):
        """The 0.85 stays 0.85 — this fix reports, it does not re-weight."""
        rep = self._fresh(tmp_path).assess([])
        assert rep.score == 0.85
        assert rep.dimensions == {"epistemic": 0.5, "reality": 1.0,
                                  "ontological": 1.0, "temporal": 1.0}
        assert rep.dissonances == [] and rep.dominant is None

    def test_marker_names_the_unmeasured_dimensions(self, tmp_path):
        rep = self._fresh(tmp_path).assess([])
        assert rep.marker() == "0.85 unmeasured: epistemic, reality, ontological, temporal"

    def test_five_resolved_outcomes_make_epistemic_measured(self, tmp_path):
        eng = self._fresh(tmp_path)
        for _ in range(4):
            eng.meta.record_confidence("coding", 0.8, outcome="success")
        assert "epistemic" in eng.assess([]).unmeasured, "four is still not enough data"
        eng.meta.record_confidence("coding", 0.8, outcome="success")
        assert "epistemic" not in eng.assess([]).unmeasured

    def test_pending_records_are_not_evidence(self, tmp_path):
        eng = self._fresh(tmp_path)
        for _ in range(20):
            eng.meta.record_confidence("coding", 0.8)   # outcome defaults to pending
        assert "epistemic" in eng.assess([]).unmeasured

    def test_an_entity_makes_ontological_measured(self, tmp_path):
        eng = self._fresh(tmp_path)
        eng.world.add_entity("thing", "system", state="alive")
        rep = eng.assess([])
        assert "ontological" not in rep.unmeasured
        assert rep.dimensions["ontological"] == 1.0   # measured AND clean

    def test_a_prediction_outcome_makes_reality_measured(self, tmp_path):
        eng = self._fresh(tmp_path)
        eng.world.add_entity("thing", "system", state="alive")
        eng.world.record_prediction("thing", "alive", "alive")
        assert "reality" not in eng.assess([]).unmeasured

    def test_events_without_transitions_are_a_measurement(self, tmp_path):
        """Zero flapping across a real window is stability, not silence."""
        rep = self._fresh(tmp_path).assess([_evt(), _evt()])
        assert "temporal" not in rep.unmeasured
        assert rep.dimensions["temporal"] == 1.0

    def test_dominant_and_unmeasured_both_render(self, tmp_path):
        eng = CoherenceEngine(FakeMeta(0.0), FakeWorld(predictions=0))
        rep = eng.assess([])
        assert rep.marker() == (f"{rep.score:.2f} dominant: epistemic "
                                "unmeasured: reality, ontological, temporal")

    def test_a_stub_without_the_accessors_counts_as_unmeasured(self):
        """An old duck-typed double proves nothing was observed — not that it was."""
        class Bare:
            def calibration_score(self): return 0.9
            def recent_prediction_error_rate(self, window_hours=24): return 0.0
            def entity_count(self): return 3
            def contradicted_entities(self): return []

        rep = CoherenceEngine(Bare(), Bare()).assess([_evt()])
        assert rep.unmeasured == ("epistemic", "reality")
        assert rep.dimensions["epistemic"] == 0.9   # the score still comes through

    def test_the_field_defaults_so_existing_constructions_keep_working(self):
        rep = CoherenceReport(0.3, {}, [], None)
        assert rep.unmeasured == ()
        assert rep.marker() == "0.30"
