"""Known-value tests for the council benchmark metric helpers.

Values are computed by hand, not asserted against the implementation:
each test documents the input, the expected arithmetic, and the expected
result. (spec section 7.4: cada função com seu teste de valor conhecido.)
"""
from __future__ import annotations

import pytest
from council_bench import cohen_kappa, confusion


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
