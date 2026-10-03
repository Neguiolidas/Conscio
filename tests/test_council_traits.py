"""Trait extraction tests (spec 2026-09-26, section 5.1 + plan T4).

Covers: one positive and one negative per trait; the REAL dev m01-m12
minimal pairs (loaded from the fixture, not pasted strings); the plan's
negation cases; the negation window (3 vs 4 words); word boundaries;
case-insensitivity; options participation; the empty-context contract;
and determinism.

A55 (negation emenda 2026-09-27): the window stops at sentence ends and
at segment joins; the comma stays inside the window (list distribution);
nobody/nothing have clause scope; n't contractions (straight and curly
apostrophe) and lack/lacks/lacking/cannot/missing/none/never are
negators; the dots of .env and 1.5 do not end a sentence.

G34 (post-trigger absence and contractions emenda 2026-09-27, H51 findings 1
and 2): post-trigger absence predicates for mitigators (none, missing,
nonexistent, absent, unavailable, not available); partitive none of
exception; absence predicates do not cancel risk traits; general negators
do not cancel post-trigger; 15 apostropheless contractions as local negators.

H53 emenda (F3): the post-trigger absence scan has NO word limit — it runs
from the trigger to the end of the sentence, so real absence at any gap
cancels the mitigator (the optimistic-side miss 'the backup is there but
missing the checksum', gap 4, is now caught).
"""
from __future__ import annotations

from dataclasses import asdict

import pytest
from council_bench import load_split

from conscio.council_traits import Traits, extract_traits

#: One positive (lights the trait) and one negative (does not) text per
#: trait. The negative texts contain no trigger of that trait.
PER_TRAIT: list[tuple[str, str, str]] = [
    ("irreversible", "delete the old artifacts", "rename the old artifacts"),
    ("blast_radius", "deploy to production", "update the changelog entry"),
    ("bypasses_checks", "push with --no-verify", "run the checks first"),
    ("unverified", "ship it, probably fine", "tested on the staging box"),
    ("data_exposure", "print the api key to the log", "rotate the logs nightly"),
    ("underspecified", "the deadline is tbd", "the size is 8 megabytes"),
    ("reversible", "take a backup first", "we copied the data first"),
    ("verified", "the tests pass locally", "the tests are pending"),
    ("low_stakes", "docs only change", "a schema migration for orders"),
]


@pytest.mark.parametrize("trait, positive, negative", PER_TRAIT,
                         ids=[t[0] for t in PER_TRAIT])
def test_one_positive_and_one_negative_per_trait(trait, positive, negative):
    assert getattr(extract_traits(positive), trait) is True
    assert getattr(extract_traits(negative), trait) is False


# The dev's real minimal pairs (plan T4 emenda, commit 1216dd5): odd id =
# mitigator present, even id = the same question with the mitigator
# negated. Loaded from the fixture — not pasted strings.
DEV_PAIRS = [
    ("m01", "m02", "reversible"),
    ("m03", "m04", "reversible"),
    ("m05", "m06", "verified"),
    ("m07", "m08", "verified"),
    ("m09", "m10", "low_stakes"),
    ("m11", "m12", "low_stakes"),
]


@pytest.mark.parametrize("odd, even, trait", DEV_PAIRS,
                         ids=[f"{a}-{b}" for a, b, _ in DEV_PAIRS])
def test_dev_minimal_pairs(odd, even, trait):
    dev = {case["id"]: case for case in load_split("dev")}
    odd_traits = extract_traits(dev[odd]["question"],
                                dev[odd].get("context", ""),
                                dev[odd].get("options"))
    even_traits = extract_traits(dev[even]["question"],
                                 dev[even].get("context", ""),
                                 dev[even].get("options"))
    assert getattr(odd_traits, trait) is True, f"{odd} must light {trait}"
    assert getattr(even_traits, trait) is False, f"{even} must not light {trait}"


def test_no_backup_turns_reversible_off():
    assert extract_traits("run it, no backup").reversible is False


def test_without_tests_turns_unverified_on():
    # Atomic phrase: the leading "without" is part of the trigger, so it
    # does not self-cancel (spec 5.1, plan T4).
    assert extract_traits("deploy without tests").unverified is True


def test_negation_window_is_three_words():
    # Negation exactly 3 words before the trigger cancels it...
    assert extract_traits("no a b backup").reversible is False
    # ...but 4 words before it does NOT.
    assert extract_traits("no a b c backup").reversible is True


def test_negated_risk_trait_stays_off():
    # Negation applies to ALL traits (Jev all_traits): "no" cancels the
    # risk trigger "untested", so unverified stays off.
    assert extract_traits("no untested changes here").unverified is False


def test_backward_only_negation_keeps_m03_lit():
    # "feature flag ... without a redeploy": the negation is AFTER the
    # trigger, and the window looks only backwards — reversible stays on.
    dev = {case["id"]: case for case in load_split("dev")}
    traits = extract_traits(dev["m03"]["question"], dev["m03"]["context"])
    assert traits.reversible is True


def test_word_boundaries():
    assert extract_traits("run on localhost").low_stakes is False
    assert extract_traits("set the locale").low_stakes is False
    assert extract_traits("fix the typography").low_stakes is False


def test_case_insensitive():
    assert extract_traits("DEPLOY TO PRODUCTION NOW").blast_radius is True


def test_trigger_in_options_lights():
    assert extract_traits("ship it?", "", ["delete the table"]).irreversible is True
    assert extract_traits("ship it?", "", None).irreversible is False


def test_empty_context_lights_nothing():
    traits = extract_traits("a plain question", "", None)
    assert traits == Traits()
    assert all(value is False for value in asdict(traits).values())


def test_determinism_five_calls():
    args = ("Should we delete the db?", "No backup, TBD, PRODUCTION",
            ["skip CI", "no backup"])
    first = extract_traits(*args)
    for _ in range(4):
        assert extract_traits(*args) == first


# ── A55: negation emenda 2026-09-27 ────────────────────────────────────
# The spec block is the source; these tests pin each rule so that the
# teeth mutants below (one per rule) have a test to kill.


def test_dev_a08_b15_nobody_cancels_verified():
    """The dev already carried the defect live: a08 (veto) and b15
    (hold) say 'nobody (has) measured/reviewed', and verified used to
    light on them. With 'nobody' as a clause negator it must not."""
    dev = {case["id"]: case for case in load_split("dev")}
    for case_id in ("a08", "b15"):
        record = dev[case_id]
        traits = extract_traits(record["question"],
                                record.get("context", ""),
                                record.get("options"))
        assert traits.verified is False, f"{case_id} must not light verified"


def test_sentence_end_stops_the_window():
    """D1: a negation in an earlier sentence does not reach a trigger in
    the next one ('No worries.' must not cancel 'Drop'; 'no staging'
    must not cancel 'Rollback')."""
    assert extract_traits("No worries. Drop the orders table.").irreversible is True
    assert extract_traits("We have no staging. Rollback is scripted.").reversible is True


def test_segment_join_stops_the_window():
    """D1: question and context are separate segments, and each option
    is its own segment — a negator never crosses the joins."""
    assert extract_traits(
        "Deploy now, yes or no", "Backup taken this morning.").reversible is True
    assert extract_traits(
        "ship", "", ["proceed without delay", "backup first"]).reversible is True


def test_contractions_cancel():
    """D2: don't / isn't / hasn't (straight apostrophe) are negators
    within the 3-word window."""
    assert extract_traits("We don't have a backup.").reversible is False
    assert extract_traits("There isn't a rollback plan.").reversible is False
    assert extract_traits("The change hasn't been reviewed.").verified is False


def test_curly_apostrophe_contraction_cancels():
    """D2: the same contractions with a curly apostrophe (’) count too
    — 'doesn’t have a backup' must not light reversible."""
    assert extract_traits("We don\u2019t have a backup.").reversible is False
    assert extract_traits("It isn\u2019t verified.").verified is False


def test_nobody_and_lack_cancel():
    """D2: 'Nobody reviewed it.' must not light verified; 'We lack a
    backup.' must not light reversible ('lack' is a negator)."""
    assert extract_traits("Nobody reviewed it.").verified is False
    assert extract_traits("We lack a backup.").reversible is False


def test_comma_stays_in_the_window():
    """The comma is INSIDE the window: 'no backup, staging or rollback'
    distributes the negation over the list, so reversible stays off.
    (Accepted cost: 'No downtime expected, rollback is ready' also
    cancels the mitigator — the conservative side.)"""
    assert extract_traits("no backup, staging or rollback").reversible is False
    assert extract_traits("No downtime expected, rollback is ready").reversible is False


def test_decimal_and_env_dots_do_not_break_the_window():
    """The dot of '1.5' and of '.env' is not a sentence end: 'no 1.5
    backup' still cancels reversible, and 'print the .env to the log'
    still lights data_exposure."""
    assert extract_traits("no 1.5 backup").reversible is False
    assert extract_traits("print the .env to the log").data_exposure is True


# ── G34: post-trigger absence negation & apostropheless contractions ────
# Spec 2026-09-26 §5.1 items 7 and 8 (H51 findings 1 and 2).


def test_post_trigger_none_cancels_reversible():
    """§5.1 it. 7: 'backup: none.' does not light reversible."""
    assert extract_traits("backup: none.").reversible is False


def test_post_trigger_nonexistent_cancels_reversible():
    """§5.1 it. 7: 'Rollback plan: nonexistent.' does not light reversible."""
    assert extract_traits("Rollback plan: nonexistent.").reversible is False


def test_post_trigger_not_available_cancels_reversible():
    """§5.1 it. 7: two-word phrase 'not available' cancels reversible."""
    assert extract_traits("The rollback is not available.").reversible is False


def test_post_trigger_missing_cancels_reversible():
    """§5.1 it. 7: 'Backup missing.' does not light reversible."""
    assert extract_traits("Backup missing.").reversible is False


def test_apostropheless_contraction_cancels_mitigator():
    """§5.1 it. 8: 'we dont have a backup' does not light reversible."""
    assert extract_traits("we dont have a backup").reversible is False


@pytest.mark.parametrize("neg", [
    "dont", "doesnt", "didnt", "isnt", "arent", "wasnt", "werent",
    "hasnt", "havent", "hadnt", "cant", "couldnt", "wont", "wouldnt", "shouldnt",
])
def test_all_apostropheless_contractions_cancel(neg: str):
    """§5.1 it. 8: each of the 15 apostropheless contractions cancels mitigators."""
    assert extract_traits(f"we {neg} have a backup").reversible is False


def test_drop_orders_table_nothing_else_keeps_irreversible_lit():
    """§5.1 it. 7: 'Drop the orders table, nothing else.' keeps irreversible lit."""
    assert extract_traits("Drop the orders table, nothing else.").irreversible is True


def test_verified_in_staging_no_issues_keeps_verified_and_staging_lit():
    """§5.1 it. 7: general negator post-trigger ('no issues') does not cancel mitigators."""
    traits = extract_traits("Verified in staging, no issues.")
    assert traits.verified is True
    assert traits.reversible is True


def test_none_of_partitive_does_not_cancel_low_stakes():
    """§5.1 it. 7: partitive 'none of' does not cancel low_stakes."""
    assert extract_traits("It is a typo fix, none of the users are affected.").low_stakes is True


def test_we_have_a_backup_keeps_reversible_lit():
    """Baseline: positive mitigator without absence predicate lights reversible."""
    assert extract_traits("We have a backup.").reversible is True


def test_post_trigger_absence_does_not_cancel_risk_traits():
    """§5.1 it. 7: post-trigger absence predicate applies ONLY to mitigators.
    Risk traits like blast_radius are never cancelled post-trigger."""
    assert extract_traits("Production is unavailable.").blast_radius is True


def test_post_trigger_sentence_boundary_stops_the_scan():
    """§5.1 it. 7 (S1): post-trigger scan stops at sentence boundary.
    Absence predicates in subsequent sentences do not cancel triggers."""
    assert extract_traits("We have a backup. None of that matters here.").reversible is True
    assert extract_traits("We have a backup. Missing: the old logs.").reversible is True


def test_post_trigger_scan_runs_to_sentence_end():
    """§5.1 it. 7 (H53 emenda, replacing S2): the post-trigger absence scan
    has NO word limit — it runs from the trigger to the end of the sentence.
    Real absence at any gap cancels the mitigator; a sentence end stops the
    scan, and a second trigger later in the text lights on its own."""
    # H53 probes: absence at gaps 2, 3, 4, 4 and 7 — all cancel now.
    assert extract_traits("the backup is missing the checksum").reversible is False
    assert extract_traits("the backup is there missing the checksum").reversible is False
    assert extract_traits("the backup is there but missing the checksum").reversible is False
    assert extract_traits("backup a b c missing").reversible is False
    assert extract_traits("backup a b c d e f missing").reversible is False
    # Sentence end stops the scan; the second trigger lights alone.
    assert extract_traits("Backups are missing. The rollback flag works").reversible is True


def test_single_sentence_second_mitigator_after_absence_lights():
    """§5.1 it. 7 (H53 probe, single sentence): 'Backups are missing and the
    rollback flag works' should light reversible via the second trigger."""
    assert extract_traits("Backups are missing and the rollback flag works").reversible is True


def test_post_trigger_absence_cancels_all_prior_mitigators_in_sentence():
    """Invariant: post-trigger absence at sentence end cancels all preceding
    mitigator triggers ('Backups and the rollback flag are missing')."""
    assert extract_traits("Backups and the rollback flag are missing").reversible is False


def test_post_trigger_unavailable_cancels_mitigators():
    """§5.1 it. 7 (S3): 'unavailable' belongs to the closed absence list.
    Cancels reversible and verified."""
    assert extract_traits("Rollback unavailable.").reversible is False
    assert extract_traits("Verification unavailable.").verified is False
    assert extract_traits("Verified unavailable.").verified is False


