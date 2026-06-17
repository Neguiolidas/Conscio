# conscio/agency/breaker.py
"""
CircuitBreaker — the paralysis instinct, F2 complete version
(spec section 5.8 / blueprint section 5).

Consecutive failures still come from the ActionLedger (single source of
truth). F2 adds: dynamic threshold via TrustMatrix, per-goal quarantine
(an intractable goal no longer paralyses the whole agent) and recovery —
global lockdown only when GLOBAL_LOCKDOWN_QUORUM goals are quarantined
at once. Release: cooldown expiry, or a fresh event relevant to the
goal's text (paralysis with recovery, not death). The pipeline still
owns the lockdown flag mutation; the breaker detects and announces.

Without db_path (F1 construction) quarantine is unavailable and the
breaker degrades to F1 behavior: any trip means global lockdown.
"""
from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .ledger import ActionLedger

DEFAULT_MAX_RETRIES = 3        # fallback when no TrustMatrix is wired
GLOBAL_LOCKDOWN_QUORUM = 3     # quarantined goals that trigger global lockdown
DEFAULT_COOLDOWN_S = 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS goal_quarantine (
    goal_fp TEXT PRIMARY KEY,
    goal_text TEXT NOT NULL DEFAULT '',
    fail_count INTEGER NOT NULL DEFAULT 0,
    locked_at REAL NOT NULL,
    cooldown_until REAL NOT NULL
);
"""

_TOKEN_RE = re.compile(r"[a-z0-9]{4,}")


class CircuitBreaker:
    def __init__(self, ledger: ActionLedger, event_bus: Any,
                 max_retries: int = DEFAULT_MAX_RETRIES, *,
                 trust: Any = None, db_path: Path | str | None = None,
                 cooldown_s: float = DEFAULT_COOLDOWN_S):
        self.ledger = ledger
        self.event_bus = event_bus
        self.max_retries = max_retries
        self.trust = trust
        self.cooldown_s = cooldown_s
        self._conn: sqlite3.Connection | None = None
        if db_path is not None:
            self._conn = sqlite3.connect(str(db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    # ── detection ──

    def threshold(self, task_type: str = "") -> int:
        if self.trust is not None and task_type:
            # never below 1: a 0-retry task is blocked upstream by the
            # pipeline, not tripped retroactively here
            return max(1, self.trust.max_action_retries(task_type))
        return self.max_retries

    def should_trip(self, goal_fp: str, task_type: str = "") -> bool:
        return (self.ledger.consecutive_failures(goal_fp)
                >= self.threshold(task_type))

    # ── collapse ──

    def trip(self, goal_fp: str, *, detail: str = "",
             goal_text: str = "") -> None:
        """Quarantine the goal and announce the intentional collapse."""
        failures = self.ledger.consecutive_failures(goal_fp)
        now = time.time()
        if self._conn is not None:
            self._conn.execute(
                "INSERT INTO goal_quarantine"
                " (goal_fp, goal_text, fail_count, locked_at, cooldown_until)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(goal_fp) DO UPDATE SET"
                " fail_count=excluded.fail_count,"
                " locked_at=excluded.locked_at,"
                " cooldown_until=excluded.cooldown_until",
                (goal_fp, goal_text[:200], failures, now,
                 now + self.cooldown_s))
            self._conn.commit()
        self.event_bus.emit(
            type="error", category="system",
            data={"message": (f"Intractable dissonance: action thread "
                              f"'{goal_fp}' collapsed after "
                              f"{failures} consecutive failures. "
                              f"{detail}").strip(),
                  "goal_fp": goal_fp,
                  "failures": failures})

    # ── quarantine state ──

    def is_quarantined(self, goal_fp: str) -> bool:
        if self._conn is None:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM goal_quarantine WHERE goal_fp=?",
            (goal_fp,)).fetchone()
        return row is not None

    def quarantined_count(self) -> int:
        if self._conn is None:
            return 0
        row = self._conn.execute(
            "SELECT COUNT(*) FROM goal_quarantine").fetchone()
        return int(row[0])

    def global_lockdown_due(self) -> bool:
        if self._conn is None:
            return True            # F1 behavior: any trip is global
        return self.quarantined_count() >= GLOBAL_LOCKDOWN_QUORUM

    # ── recovery (paralysis with recovery, not death) ──

    def review_quarantine(self) -> list[str]:
        """Release expired cooldowns and goals with fresh relevant events.

        Called at the start of every act() cycle — act() runs downstream
        of reflect(), so anything reflect()/dream emitted is visible here.
        """
        if self._conn is None:
            return []
        released: list[str] = []
        now = time.time()
        rows = self._conn.execute(
            "SELECT goal_fp, goal_text, locked_at, cooldown_until"
            " FROM goal_quarantine").fetchall()
        for goal_fp, goal_text, locked_at, cooldown_until in rows:
            if now >= cooldown_until or self._relevant_event_since(
                    goal_text, locked_at):
                self._conn.execute(
                    "DELETE FROM goal_quarantine WHERE goal_fp=?",
                    (goal_fp,))
                released.append(goal_fp)
        if released:
            self._conn.commit()
            self.event_bus.emit(
                type="system", category="system",
                data={"message": "quarantine released",
                      "goal_fps": released})
        return released

    def _relevant_event_since(self, goal_text: str,
                              locked_at: float) -> bool:
        tokens = set(_TOKEN_RE.findall(goal_text.lower()))
        if not tokens:
            return False
        since = datetime.fromtimestamp(locked_at).isoformat()
        try:
            events = self.event_bus.query(category="consciousness",
                                          since=since, limit=50)
        except Exception:
            return False           # bus without query support: cooldown only
        for evt in events or []:
            blob = str(evt).lower()
            if any(tok in blob for tok in tokens):
                return True
        return False

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
