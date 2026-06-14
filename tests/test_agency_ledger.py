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


# ── F2: verdict + approval queue ────────────────────────────────────────

def test_update_verdict_persists(tmp_path):
    led = ActionLedger(tmp_path / "conscio.db")
    rid = led.record(goal_fp="g1", tool="fs_read", args_json="{}",
                     rationale="", tier="T2", status="proposed")
    led.update_verdict(rid, "FAIL", ["destroys data", "contradicts facts"])
    row = led.get(rid)
    assert row["verdict"] == "FAIL"
    assert "destroys data" in row["verdict_reasons"]
    led.close()


def test_verdict_reasons_column_migrates_old_db(tmp_path):
    import sqlite3
    db = tmp_path / "conscio.db"
    conn = sqlite3.connect(db)        # simulate an F1 database (no column)
    conn.execute("CREATE TABLE actions (id INTEGER PRIMARY KEY, ts REAL,"
                 " goal_fp TEXT, tool TEXT, args_json TEXT, rationale TEXT,"
                 " tier TEXT, status TEXT, verdict TEXT DEFAULT '',"
                 " ok INTEGER, output TEXT DEFAULT '', error TEXT DEFAULT '',"
                 " tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,"
                 " duration_ms INTEGER DEFAULT 0, adapter TEXT DEFAULT '',"
                 " model TEXT DEFAULT '')")
    conn.commit()
    conn.close()
    led = ActionLedger(db)            # init must ALTER TABLE without crashing
    rid = led.record(goal_fp="g", tool="t", args_json="{}", rationale="",
                     tier="T2", status="proposed")
    led.update_verdict(rid, "PASS", [])
    assert led.get(rid)["verdict"] == "PASS"
    led.close()


def test_pending_lists_only_proposed(tmp_path):
    led = ActionLedger(tmp_path / "conscio.db")
    a = led.record(goal_fp="g", tool="t", args_json="{}", rationale="",
                   tier="T2", status="proposed")
    led.record(goal_fp="g", tool="t", args_json="{}", rationale="",
               tier="T2", status="failed")
    b = led.record(goal_fp="g", tool="t", args_json="{}", rationale="",
                   tier="T2", status="proposed")
    ids = [r["id"] for r in led.pending()]
    assert ids == [b, a]              # newest first, only proposed
    led.close()


# ── F4: goal_text + executed_since (Distill feed) ───────────────────────

class TestGoalText:
    def test_record_persists_goal_text(self, ledger):
        rid = ledger.record(goal_fp="fp", goal_text="Investigate: X",
                            tool="t", args_json="{}", rationale="",
                            tier="T2", status="executed", ok=True)
        assert ledger.get(rid)["goal_text"] == "Investigate: X"

    def test_goal_text_defaults_empty(self, ledger):
        rid = ledger.record(goal_fp="fp", tool="t", args_json="{}",
                            rationale="", tier="T2", status="proposed")
        assert ledger.get(rid)["goal_text"] == ""

    def test_goal_text_column_migrates_old_db(self, tmp_path):
        import sqlite3
        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)    # simulate an F1-F3 database (no column)
        conn.execute("CREATE TABLE actions (id INTEGER PRIMARY KEY, ts REAL,"
                     " goal_fp TEXT, tool TEXT, args_json TEXT,"
                     " rationale TEXT, tier TEXT, status TEXT,"
                     " verdict TEXT DEFAULT '',"
                     " verdict_reasons TEXT DEFAULT '', ok INTEGER,"
                     " output TEXT DEFAULT '', error TEXT DEFAULT '',"
                     " tokens_in INTEGER DEFAULT 0,"
                     " tokens_out INTEGER DEFAULT 0,"
                     " duration_ms INTEGER DEFAULT 0, adapter TEXT DEFAULT '',"
                     " model TEXT DEFAULT '')")
        conn.commit()
        conn.close()
        led = ActionLedger(db)        # init must ALTER TABLE without crashing
        rid = led.record(goal_fp="g", goal_text="G", tool="t", args_json="{}",
                         rationale="", tier="T2", status="proposed")
        assert led.get(rid)["goal_text"] == "G"
        led.close()


class TestExecutedSince:
    def test_filters_ok_executions_in_id_order(self, ledger):
        a = ledger.record(goal_fp="g", goal_text="G", tool="t1",
                          args_json="{}", rationale="r1", tier="T2",
                          status="executed", ok=True)
        ledger.record(goal_fp="g", goal_text="G", tool="t2", args_json="{}",
                      rationale="", tier="T2", status="failed", ok=False)
        ledger.record(goal_fp="g", goal_text="G", tool="t3", args_json="{}",
                      rationale="", tier="T2", status="proposed")
        b = ledger.record(goal_fp="g", goal_text="G", tool="t4",
                          args_json="{}", rationale="r4", tier="T2",
                          status="executed", ok=True)
        rows = ledger.executed_since(0)
        assert [r["id"] for r in rows] == [a, b]
        assert rows[0]["rationale"] == "r1" and rows[1]["tool"] == "t4"

    def test_respects_watermark(self, ledger):
        a = ledger.record(goal_fp="g", goal_text="G", tool="t1",
                          args_json="{}", rationale="", tier="T2",
                          status="executed", ok=True)
        b = ledger.record(goal_fp="g", goal_text="G", tool="t2",
                          args_json="{}", rationale="", tier="T2",
                          status="executed", ok=True)
        assert [r["id"] for r in ledger.executed_since(a)] == [b]
        assert ledger.executed_since(b) == []


# ── v1.2: concurrency hardening ─────────────────────────────────────────

def test_ledger_sets_busy_timeout(tmp_path):
    led = ActionLedger(tmp_path / "x.db")
    try:
        val = led._conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert val == 5000
    finally:
        led.close()


class TestNthRecentTs:
    def test_returns_ts_of_nth_most_recent(self, tmp_path):
        ledger = ActionLedger(tmp_path / "c.db")
        ids = [ledger.record(goal_fp="g", tool="t", args_json="{}",
                             rationale="", tier="T2", status="proposed")
               for _ in range(3)]
        nth = ledger.nth_recent_ts(3)           # oldest of the three
        assert nth == ledger.get(ids[0])["ts"]

    def test_zero_when_fewer_rows(self, tmp_path):
        ledger = ActionLedger(tmp_path / "c.db")
        assert ledger.nth_recent_ts(50) == 0.0
