"""Council trait-voice tests (calibration spec section 5.2, plan T6).

R-S1: each voice's vote changes with its OWN trait — the text trait the
spec assigns to that role — while the engine state is held constant,
and the state vote stays intact (the stricter of the two wins). The
per-voice weights that pin each test are in gates.py; the A58 report
carries the teeth table (each weight broken -> this test red ->
restored).
"""
from __future__ import annotations

import pytest

from conscio import ConsciousnessEngine
from conscio import gates


@pytest.fixture
def engine(tmp_path):
    with ConsciousnessEngine(model_name="test", storage_path=str(tmp_path)) as e:
        yield e


def _votes(result):
    return [v["vote"] for v in result["voices"]]


# ── R-S1: one voice per trait (the spec's 5.2 table) ─────────────────


def test_architect_vote_changes_with_its_own_trait(engine):
    """architect: risk = irreversible + blast_radius, mitigator =
    reversible. A delete of a production asset with no backup is the
    veto line; the same action without any trait is not."""
    control = gates._voice_architect(
        engine, "rename the changelog entry", "docs only", None)
    risky = gates._voice_architect(
        engine, "delete the production database", "no backup available", None)
    assert control["vote"] != "veto"
    assert risky["vote"] == "veto"
    assert any("trait irreversible" in c for c in risky["concerns"])
    assert any("trait blast_radius" in c for c in risky["concerns"])


def test_architect_reversible_mitigator_softens_the_vote(engine):
    """The same delete WITH a backup stays below the veto line — the
    mitigator weight is what moves it (R-S1: the vote tracks the
    voice's own traits, risk AND mitigation)."""
    risky = gates._voice_architect(
        engine, "delete the production database", "no backup available", None)
    mitigated = gates._voice_architect(
        engine, "delete the production database", "backup available", None)
    assert _gates_severity(risky["vote"]) > _gates_severity(mitigated["vote"])


def test_skeptic_vote_changes_with_its_own_trait(engine):
    """skeptic: risk = unverified + bypasses_checks, mitigator =
    verified. Skipping checks and asserting without evidence is the
    veto line; a clean text is not."""
    control = gates._voice_skeptic(
        engine, "rename the changelog entry", "docs only", None)
    risky = gates._voice_skeptic(
        engine, "push the branch", "not sure it works; push with --no-verify", None)
    assert control["vote"] != "veto"
    assert risky["vote"] == "veto"
    assert any("trait bypasses_checks" in c for c in risky["concerns"])
    assert any("trait unverified" in c for c in risky["concerns"])


def test_skeptic_verified_mitigator_softens_the_vote(engine):
    """The same unverified claim WITH verification evidence stays
    below the veto line."""
    risky = gates._voice_skeptic(
        engine, "push the branch", "not sure it works; push with --no-verify", None)
    mitigated = gates._voice_skeptic(
        engine, "push the branch", "tests pass; push with --no-verify", None)
    assert _gates_severity(risky["vote"]) > _gates_severity(mitigated["vote"])


def test_pragmatist_vote_changes_with_its_own_trait(engine):
    """pragmatist: risk = underspecified, mitigator = low_stakes. A
    declared TBD crosses the hold line; the same shape of change that
    is low stakes does not."""
    control = gates._voice_pragmatist(
        engine, "rename the changelog entry", "docs only", None)
    risky = gates._voice_pragmatist(
        engine, "ship the release", "the deadline is tbd", None)
    assert control["vote"] != "veto"
    assert _gates_severity(risky["vote"]) > _gates_severity(control["vote"])
    assert any("trait underspecified" in c for c in risky["concerns"])


def test_critic_vote_changes_with_its_own_trait(engine):
    """critic: risk = data_exposure and irreversible WITHOUT the
    reversible mitigator; no mitigator of its own. Committing the
    secrets file is the veto line; a clean text is not."""
    control = gates._voice_critic(
        engine, "rename the changelog entry", "docs only", None)
    risky = gates._voice_critic(
        engine, "commit the .env file to the repo", "it is a scratch branch", None)
    assert control["vote"] != "veto"
    assert risky["vote"] == "veto"
    assert any("trait data_exposure" in c for c in risky["concerns"])


def test_critic_irreversible_only_counts_without_reversible(engine):
    """The critic's compound risk: the same irreversible text WITH a
    backup no longer carries the 'irreversible without a reversible
    mitigation' concern."""
    naked = gates._voice_critic(
        engine, "drop the orders table", "no backup available", None)
    mitigated = gates._voice_critic(
        engine, "drop the orders table", "backup available", None)
    assert any("trait irreversible" in c for c in naked["concerns"])
    assert not any("trait irreversible" in c for c in mitigated["concerns"])


# ── council level: the verdict follows the stricter voice ────────────


def _gates_severity(vote: str) -> int:
    return {"proceed": 0, "hold": 1, "veto": 2}[vote]


def test_council_recommendation_follows_trait_veto(engine):
    """A single trait veto from any voice drags the council
    recommendation to veto (the aggregation rule itself is untouched)."""
    result = gates.council(
        engine,
        question="delete the production database",
        context="no backup available",
    )
    assert result["recommendation"] == "veto"
    vetoes = [v["role"] for v in result["voices"] if v["vote"] == "veto"]
    assert vetoes, "at least one voice must carry the trait veto"
    assert result["dissenting_voices"] == [
        v["role"] for v in result["voices"] if v["vote"] != "veto"]


def test_council_hold_without_veto_when_two_holds(engine):
    """No trait veto, but at least two trait holds -> the council
    holds (aggregation rule: >= 2 holds -> hold)."""
    result = gates.council(
        engine,
        question="ship the release",
        context="the deadline is tbd; drop the orders table; no backup available",
    )
    votes = _votes(result)
    assert votes.count("veto") == 0
    assert votes.count("hold") >= 2
    assert result["recommendation"] == "hold"


def test_council_clean_engine_still_proceeds_unanimously(engine):
    """The section-4.2 contract survives T6: a no-risk, no-trait
    council on a clean engine stays unanimous proceed (the traits are
    all off, the mitigator weights only pull scores DOWN)."""
    result = gates.council(
        engine,
        question="rename the changelog entry",
        context="docs only, private branch",
    )
    assert result["recommendation"] == "proceed"
    assert result["dissenting_voices"] == []
