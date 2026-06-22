# conscio/noosphere/catalog.py
"""Host-shared noosphere.db — the catalog of published skill artifacts.

PK = (origin_instance_id, content_sha256) (artifact identity). Re-publishing
the same content is idempotent (ON CONFLICT DO NOTHING), which preserves the
original published_ts. WAL + busy_timeout for concurrent same-host writers."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS published_skills (
    origin_instance_id TEXT NOT NULL,
    origin_label       TEXT NOT NULL,
    goal_fp            TEXT NOT NULL,
    goal_text          TEXT NOT NULL DEFAULT '',
    tool_seq           TEXT NOT NULL,
    plan_template      TEXT NOT NULL,
    published_ts       REAL NOT NULL,
    content_sha256     TEXT NOT NULL,
    artifact_json      BLOB NOT NULL,
    schema_version     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (origin_instance_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS idx_pub_goal ON published_skills(goal_fp);
CREATE INDEX IF NOT EXISTS idx_pub_logical
    ON published_skills(origin_instance_id, goal_fp, tool_seq);
"""


@dataclass(frozen=True)
class CatalogRow:
    origin_instance_id: str
    origin_label: str
    goal_fp: str
    goal_text: str
    tool_seq: str
    plan_template: str
    published_ts: float
    content_sha256: str
    artifact_json: bytes
    schema_version: int


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _row(r: sqlite3.Row) -> CatalogRow:
    return CatalogRow(
        origin_instance_id=r["origin_instance_id"], origin_label=r["origin_label"],
        goal_fp=r["goal_fp"], goal_text=r["goal_text"], tool_seq=r["tool_seq"],
        plan_template=r["plan_template"], published_ts=r["published_ts"],
        content_sha256=r["content_sha256"],
        artifact_json=bytes(r["artifact_json"]), schema_version=r["schema_version"])


def publish_rows(db: Path, rows: list[CatalogRow]) -> int:
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db)
    inserted = 0
    try:
        for r in rows:
            cur = conn.execute(
                "INSERT INTO published_skills (origin_instance_id, origin_label,"
                " goal_fp, goal_text, tool_seq, plan_template, published_ts,"
                " content_sha256, artifact_json, schema_version)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(origin_instance_id, content_sha256) DO NOTHING",
                (r.origin_instance_id, r.origin_label, r.goal_fp, r.goal_text,
                 r.tool_seq, r.plan_template, r.published_ts, r.content_sha256,
                 sqlite3.Binary(r.artifact_json), r.schema_version))
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


def read_foreign(db: Path, *, exclude_instance_id: str) -> list[CatalogRow]:
    db = Path(db)
    if not db.exists():
        return []
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM published_skills WHERE origin_instance_id != ?"
            " ORDER BY published_ts, content_sha256",
            (exclude_instance_id,)).fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def read_all(db: Path) -> list[CatalogRow]:
    db = Path(db)
    if not db.exists():
        return []
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT * FROM published_skills ORDER BY published_ts, content_sha256"
        ).fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def get(db: Path, origin_instance_id: str, content_sha256: str) -> CatalogRow | None:
    db = Path(db)
    if not db.exists():
        return None
    conn = _connect(db)
    try:
        r = conn.execute(
            "SELECT * FROM published_skills"
            " WHERE origin_instance_id=? AND content_sha256=?",
            (origin_instance_id, content_sha256)).fetchone()
    finally:
        conn.close()
    return _row(r) if r else None
