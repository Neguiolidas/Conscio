# conscio/noosphere/record_catalog.py
"""Host-shared noosphere.db — catalog of published behavioral bundles. Lives
alongside published_skills in the SAME shared db. PK (origin_instance_id,
content_sha256); ON CONFLICT DO NOTHING (idempotent snapshots). WAL +
busy_timeout for concurrent same-host writers."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS published_records (
    origin_instance_id TEXT NOT NULL,
    origin_label       TEXT NOT NULL,
    published_ts       REAL NOT NULL,
    content_sha256     TEXT NOT NULL,
    entry_count        INTEGER NOT NULL,
    window_first_ts    REAL NOT NULL DEFAULT 0,
    window_last_ts     REAL NOT NULL DEFAULT 0,
    bundle_json        BLOB NOT NULL,
    schema_version     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (origin_instance_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS idx_rec_origin
    ON published_records(origin_instance_id, published_ts);
"""


@dataclass(frozen=True)
class RecordRow:
    origin_instance_id: str
    origin_label: str
    published_ts: float
    content_sha256: str
    entry_count: int
    window_first_ts: float
    window_last_ts: float
    bundle_json: bytes
    schema_version: int


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _as_bytes(value: object) -> bytes:
    """Coerce a stored bundle_json cell to bytes. Normally a BLOB, but a
    tampered/edited row may have been coerced to TEXT (e.g. via SQLite ||);
    return its bytes so revalidation can hash it and reject (not crash)."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError(f"unexpected bundle_json cell type: {type(value).__name__}")


def _row(r: sqlite3.Row) -> RecordRow:
    return RecordRow(
        origin_instance_id=r["origin_instance_id"], origin_label=r["origin_label"],
        published_ts=r["published_ts"], content_sha256=r["content_sha256"],
        entry_count=r["entry_count"], window_first_ts=r["window_first_ts"],
        window_last_ts=r["window_last_ts"],
        bundle_json=_as_bytes(r["bundle_json"]), schema_version=r["schema_version"])


def publish_rows(db: Path, rows: list[RecordRow]) -> int:
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db)
    inserted = 0
    try:
        for r in rows:
            cur = conn.execute(
                "INSERT INTO published_records (origin_instance_id, origin_label,"
                " published_ts, content_sha256, entry_count, window_first_ts,"
                " window_last_ts, bundle_json, schema_version)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(origin_instance_id, content_sha256) DO NOTHING",
                (r.origin_instance_id, r.origin_label, r.published_ts,
                 r.content_sha256, r.entry_count, r.window_first_ts,
                 r.window_last_ts, sqlite3.Binary(r.bundle_json), r.schema_version))
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


def read_foreign(db: Path, *, exclude_instance_id: str) -> list[RecordRow]:
    db = Path(db)
    if not db.exists():
        return []
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM published_records WHERE origin_instance_id != ?"
            " ORDER BY origin_instance_id, published_ts",
            (exclude_instance_id,)).fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def get(db: Path, origin_instance_id: str, content_sha256: str) -> RecordRow | None:
    db = Path(db)
    if not db.exists():
        return None
    conn = _connect(db)
    try:
        r = conn.execute(
            "SELECT * FROM published_records"
            " WHERE origin_instance_id=? AND content_sha256=?",
            (origin_instance_id, content_sha256)).fetchone()
    finally:
        conn.close()
    return _row(r) if r else None
