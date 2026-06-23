# conscio/noosphere/quarantine.py
"""Per-instance noosphere_quarantine.db — the intake store for imported
skills. Both accepted (import_status='quarantined') and rejected imports are
recorded (audit). Nothing here is ever served, executed, or promoted in v2.2.0."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quarantine (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    content_sha256       TEXT NOT NULL,
    origin_instance_id   TEXT NOT NULL,
    origin_label         TEXT NOT NULL,
    published_ts         REAL NOT NULL,
    importer_instance_id TEXT NOT NULL,
    imported_ts          REAL NOT NULL,
    goal_fp              TEXT NOT NULL,
    goal_text            TEXT NOT NULL DEFAULT '',
    tool_seq             TEXT NOT NULL,
    plan_template        TEXT NOT NULL,
    artifact_json        BLOB NOT NULL,
    import_status        TEXT NOT NULL,
    revalidation_result  TEXT NOT NULL,
    revalidation_error   TEXT NOT NULL DEFAULT '',
    schema_version       INTEGER NOT NULL DEFAULT 1,
    trial_successes      INTEGER NOT NULL DEFAULT 0,
    trial_failures       INTEGER NOT NULL DEFAULT 0,
    last_trial_ts        REAL NOT NULL DEFAULT 0,
    last_trial_result    TEXT NOT NULL DEFAULT '',
    last_trial_error     TEXT NOT NULL DEFAULT '',
    UNIQUE(origin_instance_id, content_sha256)
);
"""

# v2.2.2: trial-stats columns added to pre-existing DBs via _migrate.
_TRIAL_COLS = {
    "trial_successes": "INTEGER NOT NULL DEFAULT 0",
    "trial_failures": "INTEGER NOT NULL DEFAULT 0",
    "last_trial_ts": "REAL NOT NULL DEFAULT 0",
    "last_trial_result": "TEXT NOT NULL DEFAULT ''",
    "last_trial_error": "TEXT NOT NULL DEFAULT ''",
}


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent ADD COLUMN for pre-v2.2.2 DBs. SQLite has no ADD COLUMN IF
    NOT EXISTS, so guard with PRAGMA table_info."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(quarantine)")}
    for col, decl in _TRIAL_COLS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE quarantine ADD COLUMN {col} {decl}")


@dataclass(frozen=True)
class QuarantineRow:
    content_sha256: str
    origin_instance_id: str
    origin_label: str
    published_ts: float
    importer_instance_id: str
    imported_ts: float
    goal_fp: str
    goal_text: str
    tool_seq: str
    plan_template: str
    artifact_json: bytes
    import_status: str
    revalidation_result: str
    revalidation_error: str
    schema_version: int
    trial_successes: int = 0
    trial_failures: int = 0
    last_trial_ts: float = 0.0
    last_trial_result: str = ""
    last_trial_error: str = ""
    id: int | None = None


def _connect(db: Path) -> sqlite3.Connection:
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _as_bytes(value: object) -> bytes:
    """Coerce a stored BLOB cell to bytes (TEXT-coerced rows survive as bytes)."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError(f"unexpected artifact_json cell type: {type(value).__name__}")


def _row(r: sqlite3.Row) -> QuarantineRow:
    return QuarantineRow(
        id=r["id"], content_sha256=r["content_sha256"],
        origin_instance_id=r["origin_instance_id"], origin_label=r["origin_label"],
        published_ts=r["published_ts"], importer_instance_id=r["importer_instance_id"],
        imported_ts=r["imported_ts"], goal_fp=r["goal_fp"], goal_text=r["goal_text"],
        tool_seq=r["tool_seq"], plan_template=r["plan_template"],
        artifact_json=_as_bytes(r["artifact_json"]), import_status=r["import_status"],
        revalidation_result=r["revalidation_result"],
        revalidation_error=r["revalidation_error"], schema_version=r["schema_version"],
        trial_successes=r["trial_successes"], trial_failures=r["trial_failures"],
        last_trial_ts=r["last_trial_ts"], last_trial_result=r["last_trial_result"],
        last_trial_error=r["last_trial_error"])


def insert(db: Path, row: QuarantineRow) -> bool:
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO quarantine (content_sha256, origin_instance_id,"
            " origin_label, published_ts, importer_instance_id, imported_ts,"
            " goal_fp, goal_text, tool_seq, plan_template, artifact_json,"
            " import_status, revalidation_result, revalidation_error,"
            " schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(origin_instance_id, content_sha256) DO NOTHING",
            (row.content_sha256, row.origin_instance_id, row.origin_label,
             row.published_ts, row.importer_instance_id, row.imported_ts,
             row.goal_fp, row.goal_text, row.tool_seq, row.plan_template,
             sqlite3.Binary(row.artifact_json), row.import_status,
             row.revalidation_result, row.revalidation_error, row.schema_version))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_rows(db: Path) -> list[QuarantineRow]:
    db = Path(db)
    if not db.exists():
        return []
    conn = _connect(db)
    try:
        rows = conn.execute("SELECT * FROM quarantine ORDER BY id").fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def get(db: Path, rowid: int) -> QuarantineRow | None:
    db = Path(db)
    if not db.exists():
        return None
    conn = _connect(db)
    try:
        r = conn.execute("SELECT * FROM quarantine WHERE id=?", (rowid,)).fetchone()
    finally:
        conn.close()
    return _row(r) if r else None


def record_trial(db: Path, rowid: int, *, passed: bool, result: str,
                 error: str, ts: float) -> bool:
    """Bump one trial counter and set the last_trial_* fields. Returns True
    iff a row was updated. `col` is from a fixed internal set — never user
    input — so the f-string carries no injection surface."""
    col = "trial_successes" if passed else "trial_failures"
    conn = _connect(db)
    try:
        cur = conn.execute(
            f"UPDATE quarantine SET {col} = {col} + 1, last_trial_ts = ?,"
            " last_trial_result = ?, last_trial_error = ? WHERE id = ?",
            (ts, result, error, rowid))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def note_trial(db: Path, rowid: int, *, result: str, error: str,
               ts: float) -> bool:
    """Record a non-counting trial note (refusal / tamper). Returns True iff a
    row was updated. Never touches the success/failure counters."""
    conn = _connect(db)
    try:
        cur = conn.execute(
            "UPDATE quarantine SET last_trial_ts = ?, last_trial_result = ?,"
            " last_trial_error = ? WHERE id = ?",
            (ts, result, error, rowid))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
