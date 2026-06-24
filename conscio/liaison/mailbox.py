# conscio/liaison/mailbox.py
"""Engine-free shared mailbox — the v2.6.0 Liaison substrate.

A single SQLite table at $HERMES_HOME/liaison.db carries directed messages
between agent instances (review_request / review_verdict). WAL + busy_timeout
mirror the noosphere catalog so concurrent same-host peers read latest-committed
rows. Read path tolerates a missing/corrupt/locked db (returns []); the write
path creates the db + table on first send. Never imports conscio.engine."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    from_instance TEXT NOT NULL,
    to_instance   TEXT NOT NULL,
    type          TEXT NOT NULL,
    payload       TEXT NOT NULL,
    ts            REAL NOT NULL,
    read_ts       REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_to
    ON messages(to_instance, type, read_ts);
"""


def default_db() -> Path:
    from ..noosphere.paths import hermes_home          # pure leaf; not the engine
    return hermes_home() / "liaison.db"


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _clamp(n: int) -> int:
    return max(1, min(n, 200))


def send(db: Path, *, from_instance: str, to_instance: str, type: str,
         payload: dict, ts: float | None = None) -> int:
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO messages (from_instance, to_instance, type, payload, ts,"
            " read_ts) VALUES (?,?,?,?,?,NULL)",
            (from_instance, to_instance, type, json.dumps(payload),
             time.time() if ts is None else ts))
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def inbox(db: Path, to_instance: str, *, types: list[str] | None = None,
          unread_only: bool = True, limit: int = 50) -> list[dict]:
    db = Path(db)
    if not db.exists():
        return []
    try:
        conn = _connect(db)
    except sqlite3.Error:
        return []
    try:
        sql = ["SELECT id, from_instance, to_instance, type, payload, ts, read_ts"
               " FROM messages WHERE to_instance=?"]
        params: list = [to_instance]
        if types:
            sql.append(" AND type IN (%s)" % ",".join("?" * len(types)))
            params += list(types)
        if unread_only:
            sql.append(" AND read_ts IS NULL")
        sql.append(" ORDER BY id DESC LIMIT ?")
        params.append(_clamp(limit))
        rows = conn.execute("".join(sql), params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"])
        except (TypeError, ValueError):
            continue                                    # unparseable row -> skip
        out.append(d)
    return out


def mark_read(db: Path, ids: list[int], read_ts: float | None = None) -> int:
    if not ids:
        return 0
    db = Path(db)
    if not db.exists():
        return 0
    try:
        conn = _connect(db)
    except sqlite3.Error:
        return 0
    ts = time.time() if read_ts is None else read_ts
    try:
        cur = conn.execute(
            "UPDATE messages SET read_ts=? WHERE read_ts IS NULL AND id IN (%s)"
            % ",".join("?" * len(ids)), [ts, *ids])
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def purge_read(db: Path, older_than_days: float = 7.0) -> int:
    """Delete READ messages older than the cutoff. Never deletes unread rows
    (an offline peer still receives). Missing/corrupt/locked db -> 0. Additive
    (v2.6.1): send/inbox/mark_read are unchanged; no schema change."""
    db = Path(db)
    if not db.exists():
        return 0
    try:
        conn = _connect(db)
    except sqlite3.Error:
        return 0
    cutoff = time.time() - older_than_days * 86400.0
    try:
        cur = conn.execute(
            "DELETE FROM messages WHERE read_ts IS NOT NULL AND read_ts < ?",
            (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
