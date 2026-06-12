# conscio/agency/ledger.py
"""
ActionLedger — append-only audit of every act() cycle (spec section 5.9,
safety rule R8). Lives in the EXISTING shared conscio.db (the same WAL
database that holds ContentStore + EventBus) — no new DB convention.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    goal_fp TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,              -- proposed|executed|rejected|failed|locked
    verdict TEXT NOT NULL DEFAULT '',  -- skeptic verdict (F2)
    verdict_reasons TEXT NOT NULL DEFAULT '',
    ok INTEGER,                        -- NULL until executed
    output TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    adapter TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_actions_goal ON actions(goal_fp, id);
CREATE INDEX IF NOT EXISTS idx_actions_tool ON actions(tool);
"""


class ActionLedger:
    def __init__(self, db_path: Path | str):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        try:                                   # F1 databases lack the column
            self._conn.execute("ALTER TABLE actions ADD COLUMN"
                               " verdict_reasons TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass                               # already present

    def record(self, *, goal_fp: str, tool: str, args_json: str,
               rationale: str, tier: str, status: str, ok: bool | None = None,
               tokens_in: int = 0, tokens_out: int = 0,
               adapter: str = "", model: str = "") -> int:
        cur = self._conn.execute(
            "INSERT INTO actions (ts, goal_fp, tool, args_json, rationale,"
            " tier, status, ok, tokens_in, tokens_out, adapter, model)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), goal_fp, tool, args_json, rationale, tier, status,
             None if ok is None else int(ok), tokens_in, tokens_out,
             adapter, model))
        self._conn.commit()
        return int(cur.lastrowid)

    def update_execution(self, row_id: int, *, ok: bool, output: str,
                         error: str, duration_ms: int, status: str) -> None:
        self._conn.execute(
            "UPDATE actions SET ok=?, output=?, error=?, duration_ms=?,"
            " status=? WHERE id=?",
            (int(ok), output, error, duration_ms, status, row_id))
        self._conn.commit()

    def update_verdict(self, row_id: int, verdict: str,
                       reasons: list[str]) -> None:
        self._conn.execute(
            "UPDATE actions SET verdict=?, verdict_reasons=? WHERE id=?",
            (verdict, "; ".join(reasons), row_id))
        self._conn.commit()

    def pending(self, limit: int = 20) -> list[dict]:
        """Approval queue (R6): proposals awaiting approve()/reject()."""
        rows = self._conn.execute(
            "SELECT * FROM actions WHERE status='proposed'"
            " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get(self, row_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM actions WHERE id=?", (row_id,)).fetchone()
        return dict(row) if row else None

    def latest(self, n: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM actions ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def count(self, task_type: str | None = None) -> int:
        if task_type:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM actions WHERE tool=?",
                (task_type,)).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM actions").fetchone()
        return int(row[0])

    def consecutive_failures(self, goal_fp: str) -> int:
        """Trailing run of status='failed' rows for this goal."""
        rows = self._conn.execute(
            "SELECT status FROM actions WHERE goal_fp=? ORDER BY id DESC"
            " LIMIT 50", (goal_fp,)).fetchall()
        streak = 0
        for row in rows:
            if row["status"] == "failed":
                streak += 1
            else:
                break
        return streak

    def close(self) -> None:
        self._conn.close()
