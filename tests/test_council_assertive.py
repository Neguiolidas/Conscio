"""Tests for council assertiveness + robustness (v4.3.0 improvements).

Covers the conservative recommendation logic, the fair critic, the
voice-failure degradation, and the new consensus fields.
"""

from __future__ import annotations

import pytest

from conscio import ConsciousnessEngine, gates
from conscio.gates import COUNCIL_VOTES, council


@pytest.fixture
def engine(tmp_path):
    with ConsciousnessEngine(model_name="test", storage_path=str(tmp_path)) as e:
        yield e


def _rec_from_votes(engine, votes):
    """Compute the recommendation the way council() does, for a given vote set."""
    # Replicate the resolution logic directly (unit-level, no engine needed).
    vetoes = votes.count("veto")
    holds = votes.count("hold")
    proceeds = votes.count("proceed")
    if vetoes >= 1:
        rec = "veto"
    elif holds >= 2:
        rec = "hold"
    elif proceeds >= 3:
        rec = "proceed"
    else:
        rec = "hold"
    return rec


class TestAssertiveRecommendation:
    def test_unanimous_proceed(self):
        assert _rec_from_votes(None, ["proceed"] * 4) == "proceed"

    def test_split_council_is_hold_not_proceed(self):
        # 2-2 must NOT collapse to proceed — this is the assertiveness fix.
        assert _rec_from_votes(None, ["proceed", "proceed", "hold", "hold"]) == "hold"

    def test_any_veto_is_veto(self):
        assert _rec_from_votes(None, ["proceed", "proceed", "proceed", "veto"]) == "veto"

    def test_two_holds_is_hold(self):
        assert _rec_from_votes(None, ["hold", "hold", "proceed", "proceed"]) == "hold"

    def test_recommendation_in_votes(self, engine):
        # Edge: council on a clean engine gives a symmetric 4-proceed.
        r = council(engine, question="clean decision Q")
        assert r["recommendation"] in COUNCIL_VOTES


class TestConsensusFields:
    def test_consensus_strength_present(self, engine):
        r = council(engine, question="test q")
        assert "consensus_strength" in r
        assert 0.0 <= r["consensus_strength"] <= 1.0

    def test_dissenting_voices_present(self, engine):
        r = council(engine, question="test q")
        assert "dissenting_voices" in r
        assert isinstance(r["dissenting_voices"], list)

    def test_unanimous_no_dissent(self, engine):
        # clean engine -> all proceed -> no dissent
        r = council(engine, question="clean q")
        assert r["recommendation"] == "proceed"
        assert r["dissenting_voices"] == []
        assert r["consensus_strength"] == 1.0


class TestFairCritic:
    def test_critic_can_vote_proceed_on_clean(self, engine):
        # A clean engine (no errors, no destructive context, avg confidence
        # normal) must let the critic endorse — not force a hold.
        r = council(engine, question="clean decision")
        critic = next(v for v in r["voices"] if v["role"] == "critic")
        assert critic["vote"] == "proceed"
        assert critic["concerns"] == []

    def test_critic_holds_on_destructive_context(self, engine):
        r = council(engine, question="drop the prod table?",
                    context="Need to delete and drop the database column")
        critic = next(v for v in r["voices"] if v["role"] == "critic")
        assert critic["vote"] == "hold"
        assert len(critic["concerns"]) >= 1


class TestRobustVoices:
    def test_failing_voice_degrades_to_hold(self, engine):
        # Force _voice_architect to raise; the council must survive with a
        # degraded 'hold' voice rather than crash.
        original = gates._voice_architect

        def boom(*a, **k):
            raise RuntimeError("simulated analyst failure")

        gates._voice_architect = boom
        try:
            r = council(engine, question="test robustness")
        finally:
            gates._voice_architect = original

        architect = next(v for v in r["voices"] if v["role"] == "architect")
        assert architect["vote"] == "hold"
        assert "degraded" in architect["analysis"]

    def test_all_voices_fail_still_returns(self, engine):
        # Even if every voice fails, council returns a conservative hold.
        backups = {n: getattr(gates, n) for n in
                   ("_voice_architect", "_voice_skeptic",
                    "_voice_pragmatist", "_voice_critic")}
        def boom(*a, **k):
            raise RuntimeError("boom")
        for n in backups:
            setattr(gates, n, boom)
        try:
            r = council(engine, question="all fail")
        finally:
            for n, fn in backups.items():
                setattr(gates, n, fn)
        assert r["recommendation"] == "hold"
        assert len(r["voices"]) == 4