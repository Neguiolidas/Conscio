"""Tests for BUG-40, BUG-47, BUG-48, BUG-49 — the four bugs found in the
v3.9.5 deep audit and fixed in v3.9.6.

En dreaded style: each bug gets (a) a red-phase proof that the bug exists
without the fix, and (b) a green-phase proof that the fix resolves it.
The red-phase is kept as a comment so the regression is documented.

Run: pytest tests/test_audit_bugs.py -v
"""
import json
import sqlite3
import tempfile
import threading
from pathlib import Path

# ── BUG-48: ActionLedger.record(status='executed') must set ok=1 ────────

def test_bug48_record_executed_sets_ok():
    """record(status='executed') without explicit ok= must default ok to 1
    so that executed_since() (which filters ok=1) can see the row.

    RED without fix: ok stays NULL, executed_since returns 0 rows.
    """
    from conscio.agency import ActionLedger

    with tempfile.TemporaryDirectory() as d:
        al = ActionLedger(Path(d) / "al.db")
        aid = al.record(
            goal_fp="fp1", tool="search", args_json="{}",
            rationale="r", tier="T2", status="executed",
            goal_text="test",
        )
        # check ok column
        conn = sqlite3.connect(str(Path(d) / "al.db"))
        row = conn.execute("SELECT ok FROM actions WHERE id=?", (aid,)).fetchone()
        conn.close()
        assert row[0] == 1, f"ok should be 1 for status='executed', got {row[0]}"

        # executed_since should find it
        rows = al.executed_since(0)
        assert len(rows) == 1, f"executed_since returned {len(rows)} rows"
        al.close()


def test_bug48_record_failed_leaves_ok_null():
    """record(status='failed') should NOT set ok=1 — it's not a success."""
    from conscio.agency import ActionLedger

    with tempfile.TemporaryDirectory() as d:
        al = ActionLedger(Path(d) / "al.db")
        aid = al.record(
            goal_fp="fp1", tool="search", args_json="{}",
            rationale="r", tier="T2", status="failed",
        )
        conn = sqlite3.connect(str(Path(d) / "al.db"))
        row = conn.execute("SELECT ok FROM actions WHERE id=?", (aid,)).fetchone()
        conn.close()
        assert row[0] is None, f"ok should be NULL for status='failed', got {row[0]}"

        # executed_since should NOT find it
        rows = al.executed_since(0)
        assert len(rows) == 0, f"executed_since should return 0 for failed, got {len(rows)}"
        al.close()


def test_bug48_record_executed_explicit_ok_false():
    """record(status='executed', ok=False) should override the default."""
    from conscio.agency import ActionLedger

    with tempfile.TemporaryDirectory() as d:
        al = ActionLedger(Path(d) / "al.db")
        aid = al.record(
            goal_fp="fp1", tool="search", args_json="{}",
            rationale="r", tier="T2", status="executed", ok=False,
        )
        conn = sqlite3.connect(str(Path(d) / "al.db"))
        row = conn.execute("SELECT ok FROM actions WHERE id=?", (aid,)).fetchone()
        conn.close()
        assert row[0] == 0, f"ok should be 0 when explicitly set, got {row[0]}"

        rows = al.executed_since(0)
        assert len(rows) == 0, "executed_since should return 0 for ok=False"
        al.close()


# ── BUG-40: SkillLibrary._rate(0/0) must return 1.0, not 0.0 ────────────

def test_bug40_rate_zero_zero_returns_one():
    """_rate with successes=0, failures=0 should return 1.0 (fresh import,
    not a failure). Without fix: returns 0.0 < MIN_SERVE_RATE → filtered.
    """
    from conscio.agency.skills import MIN_SERVE_RATE, _rate

    # simulate a row with 0/0
    with tempfile.TemporaryDirectory() as d:
        conn = sqlite3.connect(str(Path(d) / "s.db"))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE skills (id INTEGER PRIMARY KEY, successes INTEGER, failures INTEGER)")
        conn.execute("INSERT INTO skills (id, successes, failures) VALUES (1, 0, 0)")
        conn.commit()
        row = conn.execute("SELECT * FROM skills WHERE id=1").fetchone()
        rate = _rate(row)
        assert rate == 1.0, f"_rate(0/0) should be 1.0, got {rate}"
        assert rate >= MIN_SERVE_RATE, f"{rate} < MIN_SERVE_RATE={MIN_SERVE_RATE}"
        conn.close()


def test_bug40_graft_then_few_shot_serves():
    """A grafted skill with 0/0 must be served by few_shot."""
    from conscio.agency.fingerprint import goal_fingerprint
    from conscio.agency.skills import SkillLibrary

    with tempfile.TemporaryDirectory() as d:
        lib = SkillLibrary(Path(d) / "s.db")
        gt = "unique graft serve test"
        gfp = goal_fingerprint(gt)
        ts = json.dumps(["search"])
        pt = json.dumps([{"tool": "search", "args": {}, "rationale": "r"}])
        nid = lib.graft(gfp, gt, ts, pt, successes=0, failures=0)
        assert nid is not None, "graft returned None"

        shots = lib.few_shot(gt, k=3)
        assert len(shots) == 1, f"few_shot returned {len(shots)}, expected 1"
        lib._conn.close()


def test_bug40_rate_after_failure_converges():
    """After a settle(failed), rate should drop below MIN_SERVE_RATE."""
    from conscio.agency.fingerprint import goal_fingerprint
    from conscio.agency.skills import MIN_SERVE_RATE, SkillLibrary, _rate

    with tempfile.TemporaryDirectory() as d:
        lib = SkillLibrary(Path(d) / "s.db")
        gt = "convergence test goal"
        gfp = goal_fingerprint(gt)
        ts = json.dumps(["search"])
        pt = json.dumps([{"tool": "search", "args": {}, "rationale": "r"}])
        nid = lib.graft(gfp, gt, ts, pt, successes=0, failures=0)

        # settle with failure — must call few_shot first to set _served slot
        shots = lib.few_shot(gt, k=3)
        assert len(shots) == 1, "few_shot should serve grafted skill"
        class R:
            class status:
                value = "failed"
        lib.settle(R())

        row = lib._conn.execute(
            "SELECT successes, failures FROM skills WHERE id=?", (nid,)).fetchone()
        rate = _rate(row)
        assert rate == 0.0, f"rate after 0/1 should be 0.0, got {rate}"
        assert rate < MIN_SERVE_RATE, f"{rate} should be < {MIN_SERVE_RATE}"

        shots2 = lib.few_shot(gt, k=3)
        assert len(shots2) == 0, f"few_shot should return 0 after failure, got {len(shots2)}"
        lib._conn.close()


# ── BUG-49: CircuitBreaker.should_trip must check is_quarantined ─────────

def test_bug49_should_trip_after_manual_trip():
    """After trip(), should_trip() must return True even if
    consecutive_failures is still 0."""
    from conscio.agency import ActionLedger, CircuitBreaker
    from conscio.event_bus import EventBus

    with tempfile.TemporaryDirectory() as d:
        eb = EventBus(Path(d) / "eb.db")
        al = ActionLedger(Path(d) / "al.db")
        cb = CircuitBreaker(ledger=al, event_bus=eb, db_path=Path(d) / "cb.db")

        cb.trip("fp1", detail="manual", goal_text="test")

        assert cb.is_quarantined("fp1"), "is_quarantined not True after trip"
        assert cb.should_trip("fp1"), "should_trip not True after trip"
        assert not cb.should_trip("fp2"), "should_trip True for non-quarantined"
        cb.close(); al.close(); eb.close()


def test_bug49_should_trip_via_consecutive_failures():
    """should_trip still works via consecutive_failures >= threshold."""
    from conscio.agency import ActionLedger, CircuitBreaker
    from conscio.event_bus import EventBus

    with tempfile.TemporaryDirectory() as d:
        eb = EventBus(Path(d) / "eb.db")
        al = ActionLedger(Path(d) / "al.db")
        cb = CircuitBreaker(ledger=al, event_bus=eb, db_path=Path(d) / "cb.db",
                           max_retries=3)

        # record 3 failures
        for _ in range(3):
            al.record(goal_fp="fp1", tool="search", args_json="{}",
                     rationale="r", tier="T2", status="failed")

        assert al.consecutive_failures("fp1") >= 3
        assert cb.should_trip("fp1"), "should_trip via consecutive_failures"
        cb.close(); al.close(); eb.close()


# ── BUG-47: EventBus must be thread-safe ────────────────────────────────

def test_bug47_concurrent_emit():
    """4 threads × 100 emits each, no errors, all rows present."""
    from conscio.event_bus import EventBus

    with tempfile.TemporaryDirectory() as d:
        eb = EventBus(Path(d) / "t.db")
        errors = []

        def writer(start, count):
            try:
                for i in range(start, start + count):
                    eb.emit(type="host:event", category="system",
                           data={"i": i, "thread": start})
            except Exception as e:
                errors.append(f"thread {start}: {type(e).__name__}: {e}")

        threads = [threading.Thread(target=writer, args=(i * 100, 100))
                   for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        rows = eb.query(limit=2000)
        assert len(errors) == 0, f"errors: {errors}"
        assert len(rows) == 400, f"expected 400 rows, got {len(rows)}"
        eb.close()


def test_bug47_concurrent_emit_with_dedup():
    """Concurrent emit of identical data should dedup correctly."""
    from conscio.event_bus import EventBus

    with tempfile.TemporaryDirectory() as d:
        eb = EventBus(Path(d) / "t.db")
        errors = []

        def writer():
            try:
                for _ in range(50):
                    eb.emit(type="host:event", category="system",
                           data={"dedup": "same"})
            except Exception as e:
                errors.append(type(e).__name__)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        rows = eb.query(limit=2000)
        # should have 1 row (dedup) with duplicates_suppressed = 199
        assert len(errors) == 0, f"errors: {errors}"
        assert len(rows) == 1, f"expected 1 unique row, got {len(rows)}"
        suppressed = rows[0].duplicates_suppressed if hasattr(rows[0], 'duplicates_suppressed') else 0
        assert suppressed == 199, \
            f"expected 199 suppressed, got {suppressed}"
        eb.close()
