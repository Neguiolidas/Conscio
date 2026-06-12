# conscio/agency/trust.py
"""
TrustMatrix — dynamic earned trust (spec section 5.7 / blueprint section 6).

Nothing hardcoded: every number is computed on the fly from MetaCognition
primitives plus the ActionLedger. The warmup floor keeps new tools alive;
probation keeps retries=0 from becoming an absorbing state. Probation
epochs are derived from the reflection events reflect() already emits —
reflect() itself stays untouched (P6).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

PROBATION_EPOCH = 25      # reflect() cycles between probation probes
WARMUP_MIN_ROWS = 10      # below this many ledger rows the floor of 1 applies
RETRY_CEILING = 4
AUTONOMY_WINDOW = 50      # recent actions; zero trips inside it for L3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trust_probation (
    task_type TEXT PRIMARY KEY,
    last_epoch INTEGER NOT NULL DEFAULT -1
);
"""


class TrustMatrix:
    def __init__(self, meta: Any, ledger: Any, db_path: Path | str,
                 reflect_count_fn: Callable[[], int] | None = None,
                 trips_since_fn: Callable[[float], int] | None = None):
        self.meta = meta
        self.ledger = ledger
        self.reflect_count_fn = reflect_count_fn or (lambda: 0)
        self.trips_since_fn = trips_since_fn
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    # ── core formula (blueprint §6) ──

    def max_action_retries(self, task_type: str) -> int:
        penalty = min(len(self.meta.frequent_errors(min_count=3)), 2)
        raw = (1 + round(2 * self.meta.calibration_score()
                         * self.meta.accuracy(task_type)) - penalty)
        result = max(0, min(RETRY_CEILING, raw))
        if self.ledger.count(task_type) < WARMUP_MIN_ROWS:
            result = max(result, 1)         # warmup: new tools may always try
        if result == 0 and self._probation_due(task_type):
            result = 1                      # probation probe
        return result

    # ── probation (anti-deadlock, spec §5.7) ──

    def _probation_due(self, task_type: str) -> bool:
        """Grant one probe per PROBATION_EPOCH reflect() cycles.

        Idempotent within an epoch: the breaker's threshold lookup must
        see the same value the pipeline saw, or it would trip instantly.
        """
        epoch = self.reflect_count_fn() // PROBATION_EPOCH
        row = self._conn.execute(
            "SELECT last_epoch FROM trust_probation WHERE task_type=?",
            (task_type,)).fetchone()
        last = row[0] if row else -1
        if last == epoch:
            return True                     # probe already active this epoch
        if last > epoch:
            return False                    # clock went backwards: fail safe
        self._conn.execute(
            "INSERT INTO trust_probation (task_type, last_epoch)"
            " VALUES (?, ?) ON CONFLICT(task_type)"
            " DO UPDATE SET last_epoch=excluded.last_epoch",
            (task_type, epoch))
        self._conn.commit()
        return True

    def on_success(self, task_type: str) -> None:
        """A success forgives the oldest matching error pattern."""
        self.meta.expire_error(f"act:{task_type}")

    # ── earned autonomy L1/L2/L3 (spec 5.7) ──

    def autonomy_level(self, task_type: str) -> int:
        calibration = self.meta.calibration_score()
        accuracy = self.meta.accuracy(task_type)
        if not (calibration >= 0.6 and accuracy >= 0.7
                and self.ledger.count(task_type) >= 10):
            return 1
        if (calibration >= 0.75 and accuracy >= 0.85
                and self._recent_trips() == 0):
            return 3
        return 2

    def _recent_trips(self) -> int:
        """Breaker trips inside the last AUTONOMY_WINDOW actions.

        Fail-safe: without trips_since_fn wiring there is no evidence of
        a trip-free window, so L3 is unreachable (returns a sentinel 1).
        """
        if self.trips_since_fn is None:
            return 1
        return self.trips_since_fn(self.ledger.nth_recent_ts(AUTONOMY_WINDOW))

    def fast_path_ok(self) -> bool:
        """LOW-risk audit bypass gate (spec §5.6 risk gating)."""
        return self.meta.calibration_score() >= 0.75

    def close(self) -> None:
        self._conn.close()
