# conscio/mcp/seen.py
"""Persistent, bounded idempotency dedup for feed()/note() (spec §9).

Stores the FULL prior result (not just a hash) so a duplicate event.id
returns the exact same response. A separate db (not conscio.db) so its
schema + prune never touch the main migrations; user_version'd.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_USER_VERSION = 1


class SeenStore:
    def __init__(self, db_path: str | Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS mcp_seen ("
            "event_id TEXT PRIMARY KEY, last_seen_ts REAL, result_json TEXT)")
        self.conn.execute(f"PRAGMA user_version = {_USER_VERSION}")
        self.conn.commit()

    def seen(self, event_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT result_json FROM mcp_seen WHERE event_id = ?",
            (event_id,)).fetchone()
        return row[0] if row else None

    def mark(self, event_id: str, result_json: str, ts: float) -> None:
        self.conn.execute(
            "INSERT INTO mcp_seen(event_id, last_seen_ts, result_json) "
            "VALUES(?,?,?) ON CONFLICT(event_id) DO UPDATE SET "
            "last_seen_ts = excluded.last_seen_ts", (event_id, ts, result_json))
        self.conn.commit()

    def prune(self, max_rows: int = 10_000, max_age_days: int = 30,
              now: float | None = None) -> None:
        now = time.time() if now is None else now
        if max_age_days:
            self.conn.execute("DELETE FROM mcp_seen WHERE last_seen_ts < ?",
                              (now - max_age_days * 86_400,))
        if max_rows:
            self.conn.execute(
                "DELETE FROM mcp_seen WHERE event_id NOT IN ("
                "SELECT event_id FROM mcp_seen "
                "ORDER BY last_seen_ts DESC LIMIT ?)", (max_rows,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
