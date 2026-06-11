# tests/test_agency_ledger.py
"""Tests for ActionLedger — append-only action audit table (safety rule R8)."""
import pytest

from conscio.agency.ledger import ActionLedger


@pytest.fixture
def ledger(tmp_path):
    led = ActionLedger(tmp_path / "conscio.db")
    yield led
    led.close()


class TestLedger:
    def test_record_returns_rowid_and_latest_reads_back(self, ledger):
        rid = ledger.record(goal_fp="g1", tool="fs_read",
                            args_json='{"path": "a"}', rationale="r",
                            tier="T2", status="proposed")
        assert rid >= 1
        rows = ledger.latest(1)
        assert rows[0]["tool"] == "fs_read" and rows[0]["status"] == "proposed"

    def test_update_execution_marks_row(self, ledger):
        rid = ledger.record(goal_fp="g", tool="t", args_json="{}",
                            rationale="r", tier="T2", status="proposed")
        ledger.update_execution(rid, ok=True, output="done", error="",
                                duration_ms=5, status="executed")
        row = ledger.get(rid)
        assert row["ok"] == 1 and row["status"] == "executed"

    def test_consecutive_failures_counts_trailing_only(self, ledger):
        ledger.record(goal_fp="g", tool="t", args_json="{}", rationale="r",
                      tier="T2", status="failed")
        ledger.record(goal_fp="g", tool="t", args_json="{}", rationale="r",
                      tier="T2", status="executed", ok=True)
        ledger.record(goal_fp="g", tool="t", args_json="{}", rationale="r",
                      tier="T2", status="failed")
        ledger.record(goal_fp="g", tool="t", args_json="{}", rationale="r",
                      tier="T2", status="failed")
        assert ledger.consecutive_failures("g") == 2

    def test_consecutive_failures_isolated_per_goal(self, ledger):
        ledger.record(goal_fp="a", tool="t", args_json="{}", rationale="r",
                      tier="T2", status="failed")
        assert ledger.consecutive_failures("b") == 0

    def test_count_by_task_type(self, ledger):
        for _ in range(3):
            ledger.record(goal_fp="g", tool="fs_read", args_json="{}",
                          rationale="r", tier="T2", status="executed", ok=True)
        assert ledger.count(task_type="fs_read") == 3
        assert ledger.count() == 3

    def test_get_unknown_id_returns_none(self, ledger):
        assert ledger.get(999) is None
