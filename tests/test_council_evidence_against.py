"""Regression: the Council must not rubber-stamp "proceed" when the
question's own text already carries the evidence against it.

2026-10-24: in a live audit, every finding sent to the deterministic
Council came back "proceed" with four clean voices, including "adopt host
hugepages given no measured gain and one SIGBUS crash". No trait read
failure evidence or a declared missing check, so nothing reached a vote.
"""
from __future__ import annotations

import pytest
from council_bench import cohen_kappa, load_split

from conscio import ConsciousnessEngine
from conscio.council_traits import extract_traits
from conscio.gates import council


@pytest.fixture
def engine(tmp_path):
    with ConsciousnessEngine(model_name="test", storage_path=str(tmp_path)) as e:
        yield e


@pytest.mark.parametrize("text", [
    "QEMU died with SIGBUS when we tried it",
    "the build crashed twice",
    "no measured gain after the change",
    "the suite ran out of memory",
    "a confirmed regression in 5% of requests",
    "the hooks are failing",
])
def test_failure_evidence_lights(text):
    assert extract_traits(text).failure_evidence is True


@pytest.mark.parametrize("text", [
    "no regression was seen",
    "it did not crash in 13 runs",
    "4 false failures under load",
    "measured a 2x gain",
])
def test_failure_evidence_stays_off(text):
    assert extract_traits(text).failure_evidence is False


@pytest.mark.parametrize("text", [
    "the script has never been run against a copy of production data",
    "the change hasn't been reviewed",
    "the change hasn\u2019t been reviewed",
    "we have not confirmed that receivers are idempotent",
    "breaking changes that nobody on the team has reviewed yet",
    "we do not know how many clients still call it",
    "there is no usage metric for it",
])
def test_missing_check_lights(text):
    assert extract_traits(text).missing_check is True


@pytest.mark.parametrize("text", [
    "the change was reviewed by a second agent",
    "disk use was verified on the largest space",
])
def test_missing_check_stays_off(text):
    assert extract_traits(text).missing_check is False


def test_audit_finding_with_evidence_against_is_not_proceed(engine):
    # The exact question the 2026-10-24 audit sent; it came back "proceed".
    r = council(engine, question=(
        "Should we adopt host hugepages for guest RAM given no measured "
        "gain and one SIGBUS crash?"))
    assert r["recommendation"] == "veto"
    skeptic = next(v for v in r["voices"] if v["role"] == "skeptic")
    assert any("failure_evidence" in c for c in skeptic["concerns"])


def test_declared_missing_check_holds_the_council(engine):
    r = council(engine,
                question="Should we migrate the users table to the new schema this week?",
                context="The migration script has never been run against a copy of the data.")
    assert r["recommendation"] == "hold"
    # Round 2: the council reads the summed trait score, not a hold count.
    assert r["trait_score"]["hold_at"] <= r["trait_score"]["total"] < r["trait_score"]["veto_at"]


def test_clean_question_still_proceeds(engine):
    r = council(engine,
                question="Should we change the snapshot example name in the schema "
                         "from 'clean' to 'auto'?",
                context="Only auto and auto_prev are accepted; the example was wrong.")
    assert r["recommendation"] == "proceed"


def test_deterministic_dev_agreement_floor(tmp_path_factory):
    """Pin the calibration gain on the dev split (the split the weights were
    tuned on): kappa was 0.135 before the fix, 0.341 after."""
    preds, labels = [], []
    for case in load_split("dev"):
        st = tmp_path_factory.mktemp("dev")
        with ConsciousnessEngine(model_name="test", storage_path=str(st)) as eng:
            r = council(eng, question=case["question"],
                        context=case.get("context", ""), options=case.get("options"))
        assert r["mode"] == "deterministic" or r["judge_status"] == "ok"
        preds.append(r["recommendation"])
        labels.append(case["label"])
    assert cohen_kappa(preds, labels) >= 0.30
