"""Known-value tests for the council benchmark metric helpers.

Values are computed by hand, not asserted against the implementation:
each test documents the input, the expected arithmetic, and the expected
result. (spec section 7.4: cada função com seu teste de valor conhecido.)
"""
from __future__ import annotations

import pytest
from council_bench import cohen_kappa, confusion, evaluate, strong_disagreements


def test_kappa_is_one_on_perfect_agreement():
    # Identical assignments: p_o = 1, so kappa = (1 - p_e) / (1 - p_e) = 1
    # no matter what p_e is.
    a = ["proceed", "hold", "veto", "proceed", "hold", "veto", "proceed"]
    assert cohen_kappa(a, list(a)) == pytest.approx(1.0)


def test_kappa_is_zero_on_a_hand_computed_chance_case():
    # a = P P P P, b = P P H H
    # p_o = 2/4 = 0.5 (the two P/P pairs agree, nothing else)
    # p_e = P(a=P)*P(b=P) + P(a=H)*P(b=H)
    #        = 1.0 * 0.5 + 0.0 * 0.5 = 0.5
    # kappa = (0.5 - 0.5) / (1 - 0.5) = 0
    a = ["proceed", "proceed", "proceed", "proceed"]
    b = ["proceed", "proceed", "hold", "hold"]
    assert cohen_kappa(a, b) == pytest.approx(0.0)


def test_confusion_matrix_3x3_hand_computed():
    # a = P P H H V V, b = P H P H V V
    #   rows(a) \ cols(b):
    #                proceed  hold  veto
    #   proceed        1        1     0
    #   hold           1        1     0
    #   veto           0        0     2
    a = ["proceed", "proceed", "hold", "hold", "veto", "veto"]
    b = ["proceed", "hold", "proceed", "hold", "veto", "veto"]
    assert confusion(a, b) == {
        "proceed": {"proceed": 1, "hold": 1, "veto": 0},
        "hold": {"proceed": 1, "hold": 1, "veto": 0},
        "veto": {"proceed": 0, "hold": 0, "veto": 2},
    }


# ---------------------------------------------------------------------------
# A49 G4: strong_disagreements is the function that picks the cases Hermet
# judges — it gets its own known-value test.
# ---------------------------------------------------------------------------

def _case(case_id: str, origin: str, label: str,
          confidence: float = 0.9) -> dict:
    return {
        "id": case_id,
        "origin": origin,
        "label": label,
        "confidence": confidence,
        "question": "q",
        "context": "",
        "probabilities": {"proceed": 0.5, "hold": 0.25, "veto": 0.25},
    }


def test_strong_disagreements_known_values():
    # conf 0.90 counts (>=, not >), 0.89 does not; only proceed<->veto is
    # opposite (hold never is); a case with no council verdict is skipped;
    # ids come back sorted.
    cases = [
        _case("z9", "a", "veto", confidence=0.90),   # in: conf exactly 0.9
        _case("a1", "a", "proceed", confidence=0.89),  # out: conf below 0.9
        _case("m5", "b", "veto", confidence=0.95),   # out: hold is not opposite
        _case("q7", "b", "proceed", confidence=0.92),  # skipped: no prediction
        _case("b2", "c2", "veto", confidence=0.91),  # in
        _case("a0", "c2", "proceed", confidence=1.0),  # in
    ]
    preds = {"z9": "proceed", "m5": "hold", "b2": "proceed", "a0": "veto"}
    assert strong_disagreements(cases, preds) == ["a0", "b2", "z9"]


# ---------------------------------------------------------------------------
# A49 G1-G3: synthetic pass/fail tests for the pure evaluate() decision
# (no engines involved).
# ---------------------------------------------------------------------------

def test_c4_whole_fails_while_origins_pass():
    # G1: each origin pool clears the 0.40 kappa floor
    # (0.4444 each, hand-checked: p_o 0.6, p_e 0.28 per pool) but the
    # pooled marginal inflation drops the whole kappa to 0.375 — only the
    # whole-C4 check rejects.
    ab_cases = [
        _case(f"ab{i}", "a" if i < 3 else "b", label)
        for i, label in enumerate(["proceed", "veto", "veto", "hold", "hold"])
    ]
    c2_cases = [
        _case(f"c2_{i}", "c2", label)
        for i, label in enumerate(["proceed", "hold", "hold", "proceed", "proceed"])
    ]
    preds = dict(zip(
        (c["id"] for c in ab_cases),
        ["proceed", "proceed", "proceed", "hold", "hold"],
    ))
    preds.update(dict(zip(
        (c["id"] for c in c2_cases),
        ["proceed", "hold", "hold", "veto", "veto"],
    )))
    checks = evaluate(ab_cases + c2_cases, preds)["checks"]
    assert checks["c4_ab"] is True
    assert checks["c4_c2"] is True
    assert checks["c4_whole"] is False


def test_c3_by_origin_rejects_a_pool_without_verdicts():
    # G2: whole-C3 coverage passes, but the c2 pool has clean cases with
    # no council verdicts — the per-origin C3 must reject, which is what
    # stops a c2-only failure from closing the round.
    cases = [
        _case(f"ab{i}", "a" if i < 5 else "b", "proceed") for i in range(10)
    ]
    cases += [_case(f"c2_{i}", "c2", "veto") for i in range(2)]
    preds = dict(zip(
        (c["id"] for c in cases[:10]),
        ["proceed"] * 4 + ["hold"] * 4 + ["veto"] * 2,
    ))
    checks = evaluate(cases, preds)["checks"]
    assert checks["c3_whole"] is True
    assert checks["c3_c2"] is False


def test_empty_origin_pool_fails_as_not_measurable():
    # G3: no c2 cases at all — the per-origin C3 and C4 are undefined
    # (kappa would be NaN), and NaN < 0.4 is False: they must fail as
    # 'not measurable', never pass silently.
    cases = [_case(f"ab{i}", "a", "proceed") for i in range(5)]
    preds = {c["id"]: "proceed" for c in cases}
    checks = evaluate(cases, preds)["checks"]
    assert checks["c3_c2"] is False
    assert checks["c4_c2"] is False


def test_empty_clean_heldout_fails_whole_kappa():
    # H48: G3's teeth bit the per-origin pools, but the WHOLE-kappa NaN
    # branch (c4_whole_ok = not isnan(...) and ...) had no test that
    # morda. A heldout with no clean case carrying a verdict gives an
    # undefined whole kappa; the check must fail (never pass silently)
    # and the printed line must show the undefined value — assert what
    # the code actually prints ('nan' via f"{nan:.3f}').
    cases = [
        _case(f"amb{i}", "a", "proceed", confidence=0.4) for i in range(3)
    ]
    result = evaluate(cases, preds={})
    assert result["checks"]["c4_whole"] is False
    assert result["checks"]["c4_ab"] is False
    assert result["checks"]["c4_c2"] is False
    kappa_lines = [l for l in result["lines"] if l.startswith("kappa overall")]
    assert len(kappa_lines) == 1
    assert "nan" in kappa_lines[0]
