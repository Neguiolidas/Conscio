"""v4.7 Task 4 — Council Agreement & Coherence Cold-Start Migration Tests.

TDD RED file for Task 4.
Contracts under test:
1. Council agreement & recommendation:
   - agreement = 1 - normalized_entropy(vote_counts) over proceed/hold/veto.
   - agreement is a dict: {"value": float, "category": "asserted", "method": "vote_entropy"}.
   - recommendation is policy output: "proceed", "hold", or "veto".
   - recommendation_category is "asserted".
   - consensus_strength is a deprecated alias equal to agreement["value"].
   - Unanimous votes of any kind (4 proceeds, 4 holds, 4 vetoes) have agreement 1.0.
   - 4 vetoes produce agreement 1.0 AND recommendation "veto".
   - Mutante tabela velha: old heuristic table returned 0.1 on 4 vetoes.
2. Coherence cold-start:
   - CoherenceReport.confidence is a ConfidenceValue.
   - Fresh mind (all 4 dimensions unmeasured):
     * legacy scalar score is 0.85 (preserved for backward compatibility).
     * confidence.category is "none".
     * confidence.value is None.
     * confidence.samples is 0.
     * confidence.as_gate_input() raises ValueError (never feeds gates with 0.85).
   - Partially unmeasured mind:
     * confidence.category is "asserted".
     * confidence.value == score.
     * confidence.samples == measured dimension count.
   - Fully measured mind:
     * confidence.category is "asserted".
     * confidence.samples == 4.
"""

import math

import pytest

from conscio.calibration import ConfidenceValue
from conscio.coherence import CoherenceEngine
from conscio.content_layer import _RAG_DISABLED
from conscio.engine import ConsciousnessEngine
from conscio.gates import _compute_vote_agreement, council
from conscio.meta_cognition import MetaCognition
from conscio.world_model import WorldModel


@pytest.fixture
def engine(tmp_path):
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    eng.content_layer._session_rag = _RAG_DISABLED
    try:
        yield eng
    finally:
        eng.close()


# ─────────────────────────────────────────────────────────────────────────────
# Part A: Council Agreement & Recommendation Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestVoteAgreementMath:
    def test_unanimous_votes_have_agreement_one(self):
        assert _compute_vote_agreement(["proceed"] * 4) == pytest.approx(1.0)
        assert _compute_vote_agreement(["hold"] * 4) == pytest.approx(1.0)
        assert _compute_vote_agreement(["veto"] * 4) == pytest.approx(1.0)

    def test_split_votes_entropy(self):
        # 2 proceed, 2 hold: p = [0.5, 0.5, 0.0]
        # H = ln(2), H_norm = ln(2) / ln(3) ~ 0.6309, agreement = 1 - 0.6309 ~ 0.3691
        agr_2_2 = _compute_vote_agreement(["proceed", "proceed", "hold", "hold"])
        expected = 1.0 - (math.log(2) / math.log(3))
        assert agr_2_2 == pytest.approx(expected, abs=1e-4)

    def test_empty_votes_zero(self):
        assert _compute_vote_agreement([]) == 0.0


class TestCouncilContract:
    def test_council_result_has_agreement_and_recommendation_category(self, engine):
        res = council(engine, question="Should we proceed?")
        assert "agreement" in res
        assert isinstance(res["agreement"], dict)
        assert res["agreement"]["category"] == "asserted"
        assert res["agreement"]["method"] == "vote_entropy"
        assert 0.0 <= res["agreement"]["value"] <= 1.0

        assert "recommendation" in res
        assert res["recommendation_category"] == "asserted"

        # consensus_strength deprecated alias
        assert "consensus_strength" in res
        assert res["consensus_strength"] == pytest.approx(res["agreement"]["value"])

    def test_four_vetoes_produces_agreement_one_and_recommendation_veto(self, engine, monkeypatch):
        # Force all 4 voices to vote 'veto'
        def _veto_voice(eng, q, c, opts):
            return {"role": "test", "analysis": "critical flaw", "concerns": ["grave"], "vote": "veto"}

        monkeypatch.setattr("conscio.gates._voice_architect", _veto_voice)
        monkeypatch.setattr("conscio.gates._voice_skeptic", _veto_voice)
        monkeypatch.setattr("conscio.gates._voice_pragmatist", _veto_voice)
        monkeypatch.setattr("conscio.gates._voice_critic", _veto_voice)

        res = council(engine, question="Should we push to prod?")
        # Recommendation MUST be veto
        assert res["recommendation"] == "veto"
        assert res["recommendation_category"] == "asserted"

        # Agreement MUST be 1.0 (unanimous agreement to veto)
        assert res["agreement"]["value"] == pytest.approx(1.0)
        assert res["agreement"]["category"] == "asserted"
        assert res["consensus_strength"] == pytest.approx(1.0)

        # MUTANTE TABELA VELHA DEVE FALHAR:
        # Under the old inverted table, 4 vetoes gave consensus_strength = 0.1!
        assert res["consensus_strength"] != pytest.approx(0.1)

    def test_four_proceeds_produces_agreement_one_and_recommendation_proceed(self, engine, monkeypatch):
        def _proceed_voice(eng, q, c, opts):
            return {"role": "test", "analysis": "all clear", "concerns": [], "vote": "proceed"}

        monkeypatch.setattr("conscio.gates._voice_architect", _proceed_voice)
        monkeypatch.setattr("conscio.gates._voice_skeptic", _proceed_voice)
        monkeypatch.setattr("conscio.gates._voice_pragmatist", _proceed_voice)
        monkeypatch.setattr("conscio.gates._voice_critic", _proceed_voice)

        res = council(engine, question="All tests pass, ready?")
        assert res["recommendation"] == "proceed"
        assert res["agreement"]["value"] == pytest.approx(1.0)
        assert res["consensus_strength"] == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Part B: Coherence Cold-Start & ConfidenceValue Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCoherenceConfidenceValueContract:
    def test_cold_start_reports_category_none_and_null_value(self, tmp_path):
        meta = MetaCognition(storage_path=tmp_path / "meta")
        world = WorldModel(storage_path=tmp_path / "wm")
        coh = CoherenceEngine(meta, world)
        rep = coh.assess([])

        # All 4 dimensions unmeasured
        assert rep.unmeasured == ("epistemic", "reality", "ontological", "temporal")
        # Legacy scalar score preserved
        assert rep.score == 0.85

        # Structured ConfidenceValue
        assert isinstance(rep.confidence, ConfidenceValue)
        assert rep.confidence.category == "none"
        assert rep.confidence.value is None
        assert rep.confidence.samples == 0

        # Gate input invariant: category 'none' must raise ValueError
        with pytest.raises(ValueError, match="category 'none' carries no numeric value"):
            rep.confidence.as_gate_input()

    def test_partially_unmeasured_reports_category_asserted(self, tmp_path):
        meta = MetaCognition(storage_path=tmp_path / "meta")
        world = WorldModel(storage_path=tmp_path / "wm")
        # Add 1 entity -> ontological measured
        world.add_entity("svc", "component", state="running")
        coh = CoherenceEngine(meta, world)
        rep = coh.assess([])

        assert "ontological" not in rep.unmeasured
        assert len(rep.unmeasured) == 3  # partially unmeasured

        assert isinstance(rep.confidence, ConfidenceValue)
        assert rep.confidence.category == "asserted"
        assert rep.confidence.value == pytest.approx(rep.score)
        assert rep.confidence.samples == 1

        # as_gate_input returns the asserted numeric value
        assert rep.confidence.as_gate_input() == pytest.approx(rep.score)

    def test_fully_measured_reports_category_asserted_samples_four(self, tmp_path):
        meta = MetaCognition(storage_path=tmp_path / "meta")
        # 5 resolved outcomes for epistemic
        for _ in range(5):
            meta.record_confidence("task", 0.9, outcome="success")
        world = WorldModel(storage_path=tmp_path / "wm")
        world.add_entity("svc", "component", state="running")
        world.record_prediction("svc", "running", "running")

        coh = CoherenceEngine(meta, world)
        events = [{"data": {"some": "event"}}]
        rep = coh.assess(events)

        assert rep.unmeasured == ()
        assert isinstance(rep.confidence, ConfidenceValue)
        assert rep.confidence.category == "asserted"
        assert rep.confidence.samples == 4
        assert rep.confidence.value == pytest.approx(rep.score)
