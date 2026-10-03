"""Lote H (v4.8.1) — calibration helpers for Awake Mode LLM volume.

Three levers, each derived from the H1 measurements (~300 maintenance
calls/day burn the shared provider quota):

1. ``MaintenanceCooldown`` — do not regenerate the ``daemon_check`` goal
   when the last one ran less than N minutes ago (the world state rarely
   changes between heartbeats; re-proposing "everything is normal" is
   the measured waste).

2. ``maintenance_cycles`` — at most ONE maintenance cycle per run, so a
   maintenance-only heartbeat costs a single LLM proposal instead of
   ``max_cycles`` (3).

3. ``DailyCallCeiling`` — a configurable daily budget of LLM calls for
   the awake loop, counted from the action ledger (SQLite, survives any
   restart). When the ceiling is hit the awake loop keeps perceiving and
   reflecting but skips act(); the trip is REPORTED, never silent.

All three are pure/cheap: no LLM calls, only ledger reads and time math.
"""
from __future__ import annotations

import time
from typing import Any

_MAINTENANCE_CHECK = "daemon_check"
_MAINTENANCE_KEY = f"maintenance:{_MAINTENANCE_CHECK}"


class MaintenanceCooldown:
    """Gate for regenerating the daemon_check maintenance goal.

    ``should_generate`` answers whether reflect() may spawn a fresh
    ``daemon_check``: ``None`` (never executed) always allows — the very
    first check must happen — and anything inside the window blocks.
    """

    def __init__(self, minutes: int = 60) -> None:
        self.window_s = max(0, int(minutes)) * 60

    def should_generate(self, *, last_run_ts: float | None) -> bool:
        if last_run_ts is None:
            return True
        return (time.time() - float(last_run_ts)) >= self.window_s


def maintenance_cycles(cycle_summaries: list[tuple[str, str]]) -> int:
    """How many maintenance cycles a run has already consumed.

    ``cycle_summaries`` is a list of ``(goal_key, status)`` pairs for the
    cycles executed so far in this run. Only ``daemon_check`` counts; a
    failed cycle still counts — it burned a proposal, and not counting it
    would let a failing maintenance loop retry endlessly within one run.
    """
    return sum(1 for key, _status in cycle_summaries
               if key == _MAINTENANCE_KEY)


class DailyCallCeiling:
    """Daily LLM-call budget for the awake loop, read from the ledger.

    Counts ledger rows from the last 24h that represent real LLM
    proposals (tokens > 0 — T1/grammar rows are deterministic and cost
    nothing). The count is a ledger query, so the budget survives any
    process restart: the ledger IS the state.

    ``report()`` returns a human-readable trip line for the event log;
    the caller must emit it (H2: reported, never silent).
    """

    def __init__(self, *, max_calls_per_day: int,
                 ledger: Any,
                 now: float | None = None) -> None:
        self.max_calls = int(max_calls_per_day)
        self._ledger = ledger
        self._now = now if now is not None else time.time()

    def _spent(self) -> int:
        row = self._ledger._conn.execute(
            "SELECT COUNT(*) FROM actions"
            " WHERE ts > ?"
            "   AND (tokens_in > 0 OR tokens_out > 0)",
            (self._now - 24 * 3600.0,),
        ).fetchone()
        return int(row[0])

    def allows_more(self) -> bool:
        return self._spent() < self.max_calls

    def report(self) -> str:
        spent = self._spent()
        if spent < self.max_calls:
            return ""
        return (f"daily LLM ceiling reached: {spent} calls in the last 24h"
                f" (ceiling {self.max_calls}); awake loop skipping act(),"
                " perceive+reflect continue")
