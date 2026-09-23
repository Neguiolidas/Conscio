"""v4.7 Task 1 — ConfidenceValue contract and pure calibration metrics.

TDD RED file. The invariant set is frozen by the spec:
- none: value is None, metric is None
- asserted/derived: finite value in [0,1]
- measured: samples >= MIN_CALIBRATION_SAMPLES and metric in the allowed set
- ECE over M=5 equal-width bins; Brier over binary outcomes
- pending/partial never enter the metrics
- the confident-wrong adversarial must NOT look calibrated
"""

import pytest

from conscio.calibration import (
    MIN_CALIBRATION_SAMPLES,
    ConfidenceValue,
    brier,
    ece,
)


class TestConfidenceValueContract:
    def test_none_requires_null_value_and_metric(self):
        cv = ConfidenceValue.none(samples=0)
        assert cv.value is None
        assert cv.category == "none"
        assert cv.metric is None
        assert cv.samples == 0

    def test_asserted_carries_value(self):
        cv = ConfidenceValue.asserted(0.7, samples=4)
        assert cv.value == 0.7
        assert cv.category == "asserted"

    def test_none_rejects_value(self):
        with pytest.raises(ValueError):
            ConfidenceValue(category="none", value=0.5, samples=0)

    def test_asserted_rejects_none_value(self):
        with pytest.raises(ValueError):
            ConfidenceValue(category="asserted", value=None, samples=3)

    def test_value_must_be_finite(self):
        with pytest.raises(ValueError):
            ConfidenceValue(category="asserted", value=float("nan"), samples=3)

    def test_value_range(self):
        with pytest.raises(ValueError):
            ConfidenceValue(category="asserted", value=1.5, samples=3)
        with pytest.raises(ValueError):
            ConfidenceValue(category="asserted", value=-0.1, samples=3)

    def test_measured_requires_minimum_samples(self):
        with pytest.raises(ValueError):
            ConfidenceValue(category="measured", value=0.9,
                            samples=MIN_CALIBRATION_SAMPLES - 1, metric="ece")

    def test_measured_requires_known_metric(self):
        with pytest.raises(ValueError):
            ConfidenceValue(category="measured", value=0.9,
                            samples=50, metric="vibes")

    def test_measured_ok(self):
        cv = ConfidenceValue(category="measured", value=0.88,
                              samples=50, metric="ece")
        assert cv.samples == 50

    def test_serialization_round_trip(self):
        cv = ConfidenceValue(category="measured", value=0.88,
                             samples=50, metric="ece")
        import json
        d = json.loads(cv.to_json())
        assert d["category"] == "measured"
        assert d["value"] == 0.88
        assert d["samples"] == 50


class TestECE:
    def test_perfect_calibration(self):
        # confidence 0.8 in every bin, observed success 0.8 -> ECE 0
        conf = [0.8] * 10
        outcomes = [True] * 8 + [False] * 2
        assert ece(conf, outcomes) == pytest.approx(0.0, abs=1e-9)

    def test_confident_wrong_is_not_calibrated(self):
        # high confidence everywhere, all failures -> ECE ~ 0.9
        conf = [0.9] * 10
        outcomes = [False] * 10
        val = ece(conf, outcomes)
        assert val > 0.8
        # the old macro-distance score would call this 1 - |0.9 - 0.0| = 0.1
        # i.e. "bad" too; the real adversarial is below:
        assert val < 1.0

    def test_mixed_confident_wrong(self):
        # half confident-right, half confident-wrong with inverted pairing:
        # high conf on failures, low conf on successes
        conf = [0.9, 0.9, 0.9, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1]
        outcomes = [False] * 5 + [True] * 5
        val = ece(conf, outcomes)
        # old macro-distance: 1 - |0.5 - 0.5| = 1.0 "perfectly calibrated"
        assert val > 0.3

    def test_bins_boundaries(self):
        # values exactly at bin edges are assigned deterministically
        conf = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        outcomes = [False, False, False, False, False, True]
        val = ece(conf, outcomes)
        assert 0.0 <= val <= 1.0

    def test_empty_inputs_raise(self):
        with pytest.raises(ValueError):
            ece([], [])

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            ece([0.5, 0.6], [True])

    def test_confidence_range_validated(self):
        with pytest.raises(ValueError):
            ece([1.5], [True])


class TestBrier:
    def test_perfect_confident(self):
        assert brier([1.0], [True]) == pytest.approx(0.0, abs=1e-9)

    def test_confident_wrong(self):
        assert brier([1.0], [False]) == pytest.approx(1.0, abs=1e-9)

    def test_half_confident(self):
        # (0.5 - 1)^2 = 0.25 ; (0.5 - 0)^2 = 0.25
        assert brier([0.5, 0.5], [True, False]) == pytest.approx(0.25)

    def test_range(self):
        val = brier([0.7, 0.3], [True, False])
        assert 0.0 <= val <= 1.0

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            brier([], [])


class TestAdversarialMutant:
    def test_macro_distance_mutant_fails(self):
        """The exact defect measured in the audit: a confident-wrong agent
        would score 1.0 under the old 1 - |E[C] - E[Y]| formula when
        E[C] == E[Y]. ECE/Brier must NOT reward it.

        Teeth note: the mutant test must assert the SPECIFIC fix signature,
        not just "not calibrated" — the macro-distance mutant scores 1.0 on
        the anti-correlated case, which passes a bare `> 0.3` assertion.
        Assert the real ECE value range instead: the anti-correlated case
        under the real bin metric lands HIGH (~0.72-1.0) but the mutant
        lands EXACTLY 1.0 with zero bin structure — so assert both a
        bounded window AND a strictly lower value than the mutant's.
        """
        conf = [0.9, 0.9, 0.9, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1]
        outcomes = [False] * 5 + [True] * 5
        old_style = 1.0 - abs(
            sum(conf) / len(conf)
            - (sum(1 for o in outcomes if o) / len(outcomes))
        )
        assert old_style == pytest.approx(1.0)  # the old bug: "perfect"
        val = ece(conf, outcomes)
        # the real metric: high, but NOT the mutant's exact 1.0 flat score
        assert 0.3 < val < 1.0
        assert brier(conf, outcomes) > 0.25
