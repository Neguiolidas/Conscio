"""Full-fidelity, content-addressed store for raw tool observations.

Stdlib only, and deliberately free of package-relative imports: the v3.9 hook
loads this file directly with importlib so that ``conscio/__init__.py`` — which
costs ~0.28s and pulls in an embedding model — never executes on a path that
runs once per tool call.

Layout:
  blobs         content-addressed, zlib-compressed payloads (sha256 -> bytes)
  observations  one row per tool call, pointing at two blobs
  obs_fts       FTS5 over the text; scoping is an equality filter on the joined
                observations row, not an FTS term, so it cannot be confused by
                tokenizer rules
"""

from __future__ import annotations

import hashlib
import sqlite3
import zlib
from pathlib import Path

SCHEMA_VERSION = 2

# The largest real tool output measured across 13k calls was ~90 KB. 1 MiB keeps
# every genuine payload whole while refusing pathological input outright.
MAX_FIELD_BYTES = 1024 * 1024

_COMPRESS_LEVEL = 6

_DDL = (
    "CREATE TABLE IF NOT EXISTS blobs ("
    " h TEXT PRIMARY KEY, z BLOB NOT NULL, n INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS observations ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " tool TEXT NOT NULL,"
    " project TEXT NOT NULL DEFAULT '',"
    " agent TEXT NOT NULL DEFAULT 'claude-code',"
    " session_id TEXT NOT NULL,"
    " ts TEXT NOT NULL,"
    " in_h TEXT, out_h TEXT,"
    " in_n INTEGER NOT NULL DEFAULT 0,"
    " out_n INTEGER NOT NULL DEFAULT 0,"
    " truncated INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS ix_obs_session ON observations(session_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS ix_obs_project ON observations(project, id DESC)",
    "CREATE INDEX IF NOT EXISTS ix_obs_blob_in ON observations(in_h)",
    "CREATE INDEX IF NOT EXISTS ix_obs_blob_out ON observations(out_h)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS obs_fts USING fts5(tool, input, output)",
)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) an obs.db at ``path`` and ensure the schema.

    WAL plus ``synchronous=NORMAL``: durable across process crashes, and only the
    newest commits can roll back on power loss. Never corruption. The right trade
    for high-rate fire-and-forget telemetry.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=2000")
    for stmt in _DDL:
        conn.execute(stmt)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()
    return conn


def put_blob(conn: sqlite3.Connection, raw: bytes) -> tuple[str, int, bool]:
    """Store ``raw`` content-addressed. Returns (sha256_hex, stored_len, clipped)."""
    clipped = len(raw) > MAX_FIELD_BYTES
    if clipped:
        raw = raw[:MAX_FIELD_BYTES]
    h = hashlib.sha256(raw).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO blobs(h, z, n) VALUES(?,?,?)",
        (h, zlib.compress(raw, _COMPRESS_LEVEL), len(raw)),
    )
    return h, len(raw), clipped


def get_blob(conn: sqlite3.Connection, h: str) -> bytes | None:
    """Return the raw bytes for a blob hash, or None when absent."""
    row = conn.execute("SELECT z FROM blobs WHERE h=?", (h,)).fetchone()
    return zlib.decompress(row[0]) if row else None
