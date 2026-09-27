"""Trait extraction tests (spec 2026-09-26, section 5.1 + plan T4).

Covers: one positive and one negative per trait; the REAL dev m01-m12
minimal pairs (loaded from the fixture, not pasted strings); the plan's
negation cases; the negation window (3 vs 4 words); word boundaries;
case-insensitivity; options participation; the empty-context contract;
and determinism.
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
