"""v4.7 Task 3 — the fast-path must not launder global calibration into
per-action safety, and per-tool Beta posteriors must come from the ledger.

RED file. Contracts:
- a high GLOBAL calibration score may never authorize a tool with NO history;
- a tool with ledger history gets a derived Beta(1,1) posterior with attempts
  exposed;
- zero attempts -> no auto-execute (fall through to skeptic/audit);
- safe-tool skip remains asserted policy, never a probability.
"""
from pathlib import Path

import pytest

from conscio.agency.act import ActPipeline
from conscio.agency.contracts import ActionProposal
from conscio.agency.ledger import ActionLedger
from conscio.agency.tools import Risk
from conscio.calibration import ConfidenceValue


def _ledger(tmp_path: Path) -> ActionLedger:
    return ActionLedger(tmp_path / "ledger.db")


def _proposal(tool: str = "shell") -> ActionProposal:
    return ActionProposal(
        tool=tool, args={},
        rationale="test", expected_outcome="done")


class _FakeTrust:
    """Trust stub: fast-path eligible, meta with a HIGH global calibration."""

    def __init__(self, calibration: float):
        self._calibration = calibration

    def fast_path_ok(self) -> bool:
        return True

    def autonomy_level(self, task_type: str) -> int:
        return 3

    @property
    def meta(self):
        class _Meta:
            def __init__(self, calibration: float):
                # simulate the OLD contract: score() returns a float
                self._calibration = calibration

            def calibration_score(self, task_type: str = ""):
                return self._calibration

        return _Meta(self._calibration)


def _bare_pipeline(ledger: ActionLedger, calibration: float) -> ActPipeline:
    """A pipeline assembled by hand for _audit/tool_success_confidence tests."""
    pipeline = ActPipeline.__new__(ActPipeline)
    pipeline.ledger = ledger
    pipeline.trust = _FakeTrust(calibration)  # type: ignore[assignment]
    pipeline.skeptic = None
    pipeline.autonomy_cap = 2
    return pipeline


class TestFastPathGlobalCalibrationLaundering:
    def test_global_calibration_never_authorizes_unknown_tool(self):
        """The audited defect: calibration 1.0 (global, historical) used to
        fast-path ANY low-risk tool as if it were the tool's own safety."""
        Path("/tmp/t3a").mkdir(exist_ok=True)
        ledger = _ledger(Path("/tmp/t3a"))
        pipeline = _bare_pipeline(ledger, calibration=1.0)

        class _Spec:
            name = "shell"
            description = "run a command"
            risk = Risk.LOW

        verdict = pipeline._audit(_Spec(), _proposal("shell"), "goal")
        # NO history for 'shell' -> must NOT be a confident PASS from the
        # global score. audited=False PASS with calibration confidence is
        # the laundering; require the skeptic path (audited or PROPOSED).
        assert not (verdict.verdict == "PASS" and not verdict.audited
                    and verdict.confidence is not None
                    and verdict.confidence > 0.9), (
            "global calibration laundered into per-action safety")


class TestBetaPosteriorFromLedger:
    def test_tool_with_history_gets_derived_posterior(self, tmp_path):
        ledger = _ledger(tmp_path)
        # 7 successes, 1 failure for 'shell'
        for ok in (True, True, True, True, True, True, True, False):
            row = ledger.record(goal_fp="g", tool="shell", args_json="{}",
                               rationale="r", tier="L2",
                               status="executed", ok=ok)
            ledger.update_execution(row, ok=ok, output="o", error="",
                                    duration_ms=1, status="executed")
        pipeline = _bare_pipeline(ledger, calibration=0.5)

        cv = pipeline.tool_success_confidence("shell")
        assert isinstance(cv, ConfidenceValue)
        assert cv.category == "derived"
        assert cv.samples == 8
        # Beta(1,1) with 7s/1f -> (1+7)/(1+1+8) = 0.8
        assert cv.value == pytest.approx(0.8, abs=1e-9)

    def test_zero_attempts_returns_none(self, tmp_path):
        ledger = _ledger(tmp_path)
        pipeline = ActPipeline.__new__(ActPipeline)
        pipeline.ledger = ledger
        cv = pipeline.tool_success_confidence("never_ran")
        assert cv.category == "none"
        assert cv.value is None
        assert cv.samples == 0

    def test_zero_attempts_never_auto_executes(self, tmp_path):
        """The gate rule: none has no decision weight. A tool with zero
        attempts must fall through to skeptic/audit, never auto-execute."""
        ledger = _ledger(tmp_path)
        pipeline = _bare_pipeline(ledger, calibration=0.99)

        class _Spec:
            name = "fs_write"
            description = "write a file"
            risk = Risk.LOW

        cv = pipeline.tool_success_confidence("fs_write")
        assert cv.category == "none"
        with pytest.raises(ValueError):
            cv.as_gate_input()  # none has no decision weight


class TestSafeToolSkipIsAsserted:
    def test_safe_tool_skip_not_probability(self):
        Path("/tmp/t3b").mkdir(exist_ok=True)
        Path("/tmp/t3b").mkdir(exist_ok=True)
        pipeline = _bare_pipeline(_ledger(Path("/tmp/t3b")), calibration=0.2)

        class _Spec:
            name = "think"
            description = "pure reasoning"
            risk = Risk.LOW

        verdict = pipeline._audit(_Spec(), _proposal("think"), "goal")
        assert verdict.verdict == "PASS"
        assert "skip:safe_tool" in verdict.risk_flags
