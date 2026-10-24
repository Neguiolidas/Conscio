"""Round 2 of the council calibration (2026-10-24).

The deterministic council (no judge) must stand on its own: Portuguese
lexicon, scope of "local", explicit denial of a mitigator, the action that
IS the remedy, and the summed trait score. The third split (12 Strawberry
audit findings, labels justified in the file) is a regression guard here,
never a tuning set: every hold/veto finding must come back NOT proceed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conscio import ConsciousnessEngine
from conscio import gates
from conscio.council_traits import extract_traits

AUDIT = Path(__file__).parent / "fixtures" / "council_bench" / "audit_strawberry_2026-10-24.jsonl"


@pytest.fixture
def engine(tmp_path):
    with ConsciousnessEngine(model_name="test", storage_path=str(tmp_path)) as eng:
        yield eng


def _lit(q, c="", o=None):
    return {k for k, v in vars(extract_traits(q, c, o)).items() if v}


# ── traits ───────────────────────────────────────────────────────────

def test_portuguese_words_are_whole_words_and_light_traits():
    lit = _lit("Devemos apagar a tabela de produção agora?",
               "Não há backup e ninguém testou a restauração.")
    assert {"irreversible", "blast_radius", "data_loss_risk", "mitigator_denied"} <= lit


def test_portuguese_negation_cancels_failure_evidence():
    assert "failure_evidence" not in _lit("Subir a versão nova?", "O app não travou nos testes.")
    assert "failure_evidence" in _lit("Subir a versão nova?", "O app travou duas vezes.")


def test_local_api_is_not_low_stakes_but_local_scratch_is():
    assert "low_stakes" not in _lit("Keep the local API open?", "It listens on the local port.")
    assert "low_stakes" in _lit("Delete the build artifacts in my local scratch directory?")


def test_cross_site_reachable_local_api_is_security_exposure():
    lit = _lit("Should we keep the local panel API without Host/Origin checks, since it only listens on 127.0.0.1?",
               "Any web page open in a browser on this host could POST cross-site.")
    assert "security_exposure" in lit
    assert "security_exposure" not in _lit(
        "Add a health endpoint?", "Additive change, no auth bypass, no data exposure.")


def test_denied_mitigator_is_a_risk_but_clause_negation_is_not():
    assert "mitigator_denied" in _lit("Push straight to the branch?",
                                      "This is not a private branch: two agents build on it.")
    assert "mitigator_denied" in _lit("Refactor?", "No rollback plan mentioned.")
    assert "mitigator_denied" not in _lit("Clean old files?", "Nothing is removed in dry-run mode.")


def test_revert_of_the_breaking_commit_is_remediation():
    assert "remediation" in _lit("Should we revert the commit that broke the nightly build?")


# ── council ──────────────────────────────────────────────────────────

def test_revert_that_is_the_fix_proceeds(engine):
    r = gates.council(engine, question="Should we revert the commit that broke the nightly build?",
                      context="The revert applies cleanly and the full test suite passes after it.")
    assert r["recommendation"] == "proceed"


def test_unprotected_local_api_is_vetoed(engine):
    r = gates.council(engine,
                      question="Should we keep the local panel API without Host/Origin checks, since it only listens on 127.0.0.1?",
                      context="Any web page open in a browser on this host could POST /api/act cross-site.")
    assert r["recommendation"] == "veto"
    critic = next(v for v in r["voices"] if v["role"] == "critic")
    assert any("security_exposure" in c for c in critic["concerns"])


def test_pending_open_finding_is_not_handed_over(engine):
    r = gates.council(engine, question="Should we hand over the cold-boot path as is?",
                      context="[open] A cold boot leaves the display off; the screen stays black until someone presses power.")
    assert r["recommendation"] != "proceed"


def test_broad_scope_alone_holds(engine):
    r = gates.council(engine, question="Should we raise the retention cap for every producer?",
                      context="The cap stays configurable.")
    # blast_radius 2 - reversible 2 = 0 -> proceed; without the mitigator -> hold
    assert r["recommendation"] == "proceed"
    r2 = gates.council(engine, question="Should we raise the retention cap for every producer?")
    assert r2["recommendation"] == "hold"


def test_result_carries_trait_score_and_voice_scores(engine):
    r = gates.council(engine, question="Should we drop the production orders table?",
                      context="Orders from this week exist only in that table.")
    ts = r["trait_score"]
    assert ts["hold_at"] == gates.COUNCIL_HOLD_AT and ts["veto_at"] == gates.COUNCIL_VETO_AT
    assert ts["total"] == sum(v["trait_score"] for v in r["voices"])
    assert all("state_vote" in v for v in r["voices"])
    assert r["recommendation"] == "veto"


def test_council_trait_scores_is_pure_and_matches_council(engine):
    q, c = "Should we skip the smoke tests?", "The reviewer has not reviewed it."
    scores = gates.council_trait_scores(q, c)
    r = gates.council(engine, question=q, context=c)
    assert {v["role"]: v["trait_score"] for v in r["voices"]} == scores


def test_audit_split_hold_and_veto_findings_never_proceed(engine):
    cases = [json.loads(line) for line in AUDIT.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(cases) == 12
    for case in cases:
        if case["label"] == "proceed":
            continue
        r = gates.council(engine, question=case["question"], context=case["context"])
        assert r["recommendation"] != "proceed", case["id"]
