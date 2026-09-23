"""v4.7 Task 2 — MetaCognition calibration migration tests.

TDD RED file for Task 2.
Contracts under test:
1. calibration(task_type="") -> ConfidenceValue
   - category "none", value=None, metric=None when samples < MIN_CALIBRATION_SAMPLES (cold start).
   - category "measured", metric="ece", samples=N, value=ECE when samples >= MIN_CALIBRATION_SAMPLES.
   - Pending records filtered out; partial records excluded from binary ECE/Brier calculation.
   - task_type filtering isolates specific domains.
2. calibration_score(task_type="") -> float | None
   - Returns None when samples < MIN_CALIBRATION_SAMPLES (never fake 0.5 prior).
   - Returns 1.0 - ECE when measured.
3. Adversarial mutant with real teeth:
   - Confident-wrong sequence where old macro-distance returned 1.0 ("perfect").
   - ECE must punish this: ECE > 0.3, calibration_score() < 0.7, never 1.0.
   - Mutant that restores macro-distance 1 - |E[C] - E[Y]| must FAIL.
4. Consumers:
   - GoalGenerator.compute_meta_score handles calibration=None safely.
   - GoalGenerator.score_all_goals handles calibration=None safely.
   - MetaCognition.summary() formats safely when calibration_score() is None.
   - MetaCognition.status()["calibration"] is None on cold start.
   - average_confidence() and accuracy() remain descriptive non-gate statistics.
"""

import pytest

from conscio.calibration import (
    MIN_CALIBRATION_SAMPLES,
    ConfidenceValue,
)
from conscio.goal_generator import Drive, Goal, GoalGenerator, GoalPriority
from conscio.meta_cognition import MetaCognition


@pytest.fixture
def meta(tmp_path):
    return MetaCognition(storage_path=tmp_path)


class TestMetaCognitionColdStart:
    def test_empty_calibration_is_none_category(self, meta):
        cv = meta.calibration()
        assert isinstance(cv, ConfidenceValue)
        assert cv.category == "none"
        assert cv.value is None
        assert cv.metric is None
        assert cv.samples == 0
        assert cv.lower_is_better is False

    def test_empty_calibration_score_is_none(self, meta):
        # Cold start must return None, NEVER a fake 0.5 prior
        assert meta.calibration_score() is None

    def test_insufficient_samples_returns_none_category(self, meta):
        # 4 samples < MIN_CALIBRATION_SAMPLES (5)
        for _ in range(MIN_CALIBRATION_SAMPLES - 1):
            meta.record_confidence("coding", 0.8, "success")
        cv = meta.calibration()
        assert cv.category == "none"
        assert cv.value is None
        assert cv.samples == MIN_CALIBRATION_SAMPLES - 1
        assert meta.calibration_score() is None

    def test_has_calibration_evidence_boolean(self, meta):
        assert meta.has_calibration_evidence() is False
        for _ in range(MIN_CALIBRATION_SAMPLES - 1):
            meta.record_confidence("coding", 0.8, "success")
        assert meta.has_calibration_evidence() is False
        meta.record_confidence("coding", 0.8, "success")
        assert meta.has_calibration_evidence() is True


class TestMetaCognitionFiltering:
    def test_pending_records_are_ignored(self, meta):
        # 10 pending records: no outcomes resolved yet
        for _ in range(10):
            meta.record_confidence("coding", 0.9, "pending")
        cv = meta.calibration()
        assert cv.category == "none"
        assert cv.value is None
        assert cv.samples == 0
        assert meta.calibration_score() is None

    def test_partial_records_are_excluded_from_binary_metrics(self, meta):
        # 4 binary records + 5 partial records: binary samples = 4 < 5 -> none
        for _ in range(4):
            meta.record_confidence("coding", 0.9, "success")
        for _ in range(5):
            meta.record_confidence("coding", 0.8, "partial")
        cv = meta.calibration("coding")
        assert cv.category == "none"
        assert cv.value is None
        assert cv.samples == 4
        assert meta.calibration_score("coding") is None

    def test_task_type_filtering(self, meta):
        # 5 coding records, 2 trading records
        for _ in range(5):
            meta.record_confidence("coding", 0.8, "success")
        for _ in range(2):
            meta.record_confidence("trading", 0.5, "failure")

        # coding has >= 5 samples -> measured
        cv_coding = meta.calibration("coding")
        assert cv_coding.category == "measured"
        assert cv_coding.samples == 5

        # trading has 2 samples -> none
        cv_trading = meta.calibration("trading")
        assert cv_trading.category == "none"
        assert cv_trading.samples == 2
        assert meta.calibration_score("trading") is None

        # global ("") has 7 samples -> measured
        cv_global = meta.calibration()
        assert cv_global.category == "measured"
        assert cv_global.samples == 7


class TestMetaCognitionMeasuredCalibration:
    def test_well_calibrated_returns_measured_ece(self, meta):
        # 8 successes at 0.8, 2 failures at 0.8 -> perfectly calibrated in that bin
        for _ in range(8):
            meta.record_confidence("task", 0.8, "success")
        for _ in range(2):
            meta.record_confidence("task", 0.8, "failure")

        cv = meta.calibration()
        assert cv.category == "measured"
        assert cv.metric == "ece"
        assert cv.samples == 10
        assert cv.lower_is_better is True
        assert cv.value == pytest.approx(0.0, abs=1e-6)

        score = meta.calibration_score()
        assert score is not None
        assert score == pytest.approx(1.0, abs=1e-6)

    def test_brier_score_exposed(self, meta):
        for _ in range(5):
            meta.record_confidence("task", 1.0, "success")
        assert meta.brier_score() == pytest.approx(0.0, abs=1e-6)


class TestAdversarialMutantWithTeeth:
    def test_macro_distance_mutant_fails(self, meta):
        """Audit defect reproduction:
        10 samples: 5 confident-wrong (conf 0.9, failure) + 5 underconfident-right (conf 0.1, success).
        Macro-distance:
            avg_conf = (5*0.9 + 5*0.1)/10 = 0.5
            accuracy = 5/10 = 0.5
            1 - |0.5 - 0.5| = 1.0 (FATALLY rewarded as 'perfectly calibrated')
        Real ECE:
            Bin [0.0, 0.2): mean_conf=0.1, acc=1.0, gap=0.9, weight=0.5
            Bin [0.8, 1.0]: mean_conf=0.9, acc=0.0, gap=0.9, weight=0.5
            ECE = 0.5 * 0.9 + 0.5 * 0.9 = 0.90
            calibration_score = 1.0 - 0.90 = 0.10
        """
        for _ in range(5):
            meta.record_confidence("adversarial", 0.9, "failure")
        for _ in range(5):
            meta.record_confidence("adversarial", 0.1, "success")

        cv = meta.calibration("adversarial")
        assert cv.category == "measured"
        assert cv.metric == "ece"
        assert cv.samples == 10
        assert cv.lower_is_better is True

        # ECE must punish this: ECE is ~0.9, strictly in (0.7, 1.0]
        assert 0.7 < cv.value <= 1.0

        # calibration_score must be low (~0.1), NEVER 1.0
        score = meta.calibration_score("adversarial")
        assert score is not None
        assert 0.0 <= score < 0.3
        assert score != pytest.approx(1.0)


class TestConsumersMigration:
    def test_goal_compute_meta_score_with_none_calibration(self):
        goal = Goal("Test", Drive.CURIOSITY, priority=GoalPriority.HIGH)
        # calibration=None should not crash and apply neutral penalty (cal_penalty = 1.0)
        score_none = goal.compute_meta_score(confidence=0.8, calibration=None)
        assert 0.0 < score_none <= 1.0

        # Compare with known calibration values
        score_high = goal.compute_meta_score(confidence=0.8, calibration=1.0)
        score_low = goal.compute_meta_score(confidence=0.8, calibration=0.2)
        assert score_high == pytest.approx(score_none)  # 1.0 calibration has no penalty
        assert score_low < score_none  # low calibration has penalty

    def test_goal_generator_score_all_goals_with_none_calibration(self, tmp_path):
        gg = GoalGenerator(tmp_path)
        gg.generate_from_curiosity("Anomaly A")
        gg.generate_from_maintenance("check", "system")
        # Cold start: calibration=None must not raise
        gg.score_all_goals(confidence=0.8, calibration=None)
        for g in gg.active_goals():
            assert g.meta_score > 0

    def test_meta_summary_cold_start_formatting(self, meta):
        # With zero data, calibration_score() is None. summary() must not throw TypeError.
        summary = meta.summary()
        assert "Calibration: none" in summary or "Calibration: None" in summary

    def test_meta_status_cold_start(self, meta):
        st = meta.status()
        assert st["calibration"] is None

    def test_descriptive_methods_remain_scalars(self, meta):
        # average_confidence and accuracy remain descriptive statistics
        assert meta.average_confidence() == 0.5
        assert meta.accuracy() == 0.5
