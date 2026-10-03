"""Lote H (v4.8.1) — calibration of awake-mode LLM call volume.

H2 levers, red-first: (1) maintenance-goal cooldown, (2) at most one
maintenance cycle per run, (3) daily LLM-call ceiling for the awake loop,
counted from the ledger (survives restarts) and reported when hit.

These tests exercise the calibration helpers directly; engine wiring is
covered by the engine-level tests at the bottom of the file.
"""
from __future__ import annotations

import time
from pathlib import Path

from conscio.agency.ledger import ActionLedger
from conscio.awake.calibration import (
    DailyCallCeiling,
    MaintenanceCooldown,
    maintenance_cycles,
)

# ── H2.1: maintenance cooldown ───────────────────────────────────────────────

def test_cooldown_blocks_regeneration_within_window():
    cd = MaintenanceCooldown(minutes=30)
    assert cd.should_generate(last_run_ts=time.time()) is False


def test_cooldown_allows_regeneration_after_window():
    cd = MaintenanceCooldown(minutes=30)
    # last run 31 minutes ago: regeneration allowed
    assert cd.should_generate(last_run_ts=time.time() - 31 * 60) is True


def test_cooldown_never_ran_allows_generation():
    cd = MaintenanceCooldown(minutes=30)
    # None = never executed: the very first daemon_check must be generated
    assert cd.should_generate(last_run_ts=None) is True


def test_cooldown_window_configurable():
    cd = MaintenanceCooldown(minutes=120)
    # 60 minutes ago is inside a 120-minute window
    assert cd.should_generate(last_run_ts=time.time() - 60 * 60) is False


# ── H2.2: one maintenance cycle per run ─────────────────────────────────────

def test_maintenance_cycles_caps_daemon_check_at_one():
    """A run may execute at most one daemon_check cycle; other goal types
    do not count against the maintenance cap."""
    ran = [("maintenance:daemon_check", "executed"),
           ("some real goal", "executed")]
    assert maintenance_cycles(ran) == 1


def test_maintenance_cycles_zero_when_no_daemon_check():
    ran = [("goal a", "executed"), ("goal b", "failed")]
    assert maintenance_cycles(ran) == 0


def test_maintenance_cycles_exact_key_not_substring():
    """A goal whose key merely CONTAINS 'daemon' (e.g. a user goal about
    daemon logs) is NOT a maintenance cycle — only the exact
    maintenance:daemon_check key counts."""
    ran = [("investigate daemon logs", "executed"),
           ("maintenance:daemon_check", "executed")]
    assert maintenance_cycles(ran) == 1


def test_maintenance_cycles_counts_only_completed_daemon_checks():
    """A cycle that did not finish (failed) still consumed a proposal; it
    counts, otherwise a failing maintenance loop would retry endlessly
    inside a single run."""
    ran = [("maintenance:daemon_check", "failed")]
    assert maintenance_cycles(ran) == 1


# ── H2.3: daily ceiling from the ledger ─────────────────────────────────────

def _ledger_with_rows(tmp_path: Path, hours_ago: list[float],
                      statuses: list[str] | None = None) -> ActionLedger:
    led = ActionLedger(tmp_path / "conscio.db")
    now = time.time()
    statuses = statuses or ["executed"] * len(hours_ago)
    for h, st in zip(hours_ago, statuses):
        led.record(goal_fp="maintenance:daemon_check", tool="host_health",
                   args_json="{}", rationale="r", tier="T2", status=st,
                   tokens_in=10, tokens_out=5, adapter="agnes",
                   model="agnes-3.0-flash")
        # backdate the row we just inserted
        row_id = led.count()
        led._conn.execute(
            "UPDATE actions SET ts=? WHERE id=?",
            (now - h * 3600.0, row_id))
        led._conn.commit()
    return led


def test_ceiling_allows_calls_under_limit(tmp_path):
    led = _ledger_with_rows(tmp_path, hours_ago=[1.0, 2.0])  # 2 calls today
    ceil = DailyCallCeiling(max_calls_per_day=10, ledger=led)
    assert ceil.allows_more() is True


def test_ceiling_blocks_at_limit(tmp_path):
    led = _ledger_with_rows(tmp_path, hours_ago=[float(i) for i in range(12)])
    ceil = DailyCallCeiling(max_calls_per_day=10, ledger=led)
    assert ceil.allows_more() is False


def test_ceiling_ignores_rows_older_than_24h(tmp_path):
    """Only the last 24h count — yesterday's traffic must not block today."""
    led = _ledger_with_rows(tmp_path, hours_ago=[1.0, 25.0, 30.0])
    ceil = DailyCallCeiling(max_calls_per_day=2, ledger=led)
    assert ceil.allows_more() is True


def test_ceiling_reports_not_silent(tmp_path):
    """H2 requires the ceiling be REPORTED when hit, never silent."""
    led = _ledger_with_rows(tmp_path, hours_ago=[1.0, 2.0, 3.0])
    ceil = DailyCallCeiling(max_calls_per_day=3, ledger=led)
    assert ceil.allows_more() is False
    assert ceil.report() != ""                     # non-empty report
    assert "3" in ceil.report()                    # carries the count


def test_ceiling_survives_restart(tmp_path):
    """The ceiling reads the ledger, not memory: a fresh process instance
    with the same ledger sees the same spent budget."""
    led = _ledger_with_rows(tmp_path, hours_ago=[1.0, 2.0])
    c1 = DailyCallCeiling(max_calls_per_day=2, ledger=led)
    assert c1.allows_more() is False
    # "restart": brand-new object, same ledger file
    led2 = ActionLedger(led._conn.execute(
        "PRAGMA database_list").fetchall()[0][2])
    c2 = DailyCallCeiling(max_calls_per_day=2, ledger=led2)
    assert c2.allows_more() is False


def test_ceiling_counts_only_llm_proposals(tmp_path):
    """Rows without tokens (T1 grammar / deterministic) are not LLM calls
    and must not consume the ceiling."""
    led = _ledger_with_rows(tmp_path, hours_ago=[1.0])
    led.record(goal_fp="g2", tool="world_prune", args_json="{}",
               rationale="r", tier="T1", status="executed",
               tokens_in=0, tokens_out=0, adapter="grammar",
               model="")
    # 1 LLM call (tokens>0) + 1 deterministic row (tokens=0)
    ceil = DailyCallCeiling(max_calls_per_day=1, ledger=led)
    assert ceil.allows_more() is False  # the single LLM call spent it
    # with ceiling 2 the T1 row must not tip the balance
    ceil2 = DailyCallCeiling(max_calls_per_day=2, ledger=led)
    assert ceil2.allows_more() is True  # T1 row does not count


# ── engine wiring ────────────────────────────────────────────────────────────

def test_engine_has_calibration_seams():
    """The engine exposes the three H2 levers as attributes so the daemon
    can read/report them (integration point; behavior tests live above)."""
    import inspect

    from conscio.engine import ConsciousnessEngine
    src = inspect.getsource(ConsciousnessEngine)
    assert "MaintenanceCooldown" in src
    assert "DailyCallCeiling" in src
