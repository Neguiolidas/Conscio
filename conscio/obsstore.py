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
    # Migration runs *before* the DDL: on a v1 file the index on in_h would be
    # created against a table that has no such column and raise.
    migrate(conn)
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


def _clip(text: str) -> tuple[bytes, str, bool]:
    """Return (bytes to store, text to index, was_clipped) for one field.

    The blob and the FTS row must carry the *same* content: indexing the full
    text while storing a clipped blob would let a 10 MB payload into the index
    that no blob can ever return. Clipping happens once, on bytes, and the text
    is decoded back from those exact bytes — so a cut mid-codepoint degrades to
    U+FFFD instead of raising.
    """
    raw = (text or "").encode("utf-8", "replace")
    if len(raw) <= MAX_FIELD_BYTES:
        return raw, text or "", False
    raw = raw[:MAX_FIELD_BYTES]
    return raw, raw.decode("utf-8", "replace"), True


_SCOPES = ("session", "project", "all")


def _insert_observation(
    conn: sqlite3.Connection,
    *,
    tool: str,
    input_text: str,
    output_text: str,
    project: str,
    agent: str,
    session_id: str,
    ts: str,
    truncated: bool = False,
) -> int:
    """Insert one observation without committing. Returns its id.

    Separate from ``put_observation`` so ``migrate`` can write many rows inside a
    single transaction: a migration that committed per row and then crashed would
    leave the source table half-drained with no way to resume.
    """
    in_raw, in_txt, in_clip = _clip(input_text)
    out_raw, out_txt, out_clip = _clip(output_text)
    in_h, in_n, _ = put_blob(conn, in_raw)
    out_h, out_n, _ = put_blob(conn, out_raw)
    cur = conn.execute(
        "INSERT INTO observations"
        "(tool, project, agent, session_id, ts, in_h, out_h, in_n, out_n, truncated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (tool[:256], project[:256], agent[:64], session_id, ts,
         in_h, out_h, in_n, out_n, int(truncated or in_clip or out_clip)),
    )
    row = int(cur.lastrowid or 0)
    conn.execute(
        "INSERT INTO obs_fts(rowid, tool, input, output) VALUES(?,?,?,?)",
        (row, tool[:256], in_txt, out_txt),
    )
    return row


def put_observation(conn: sqlite3.Connection, **kwargs) -> int:
    """Insert one observation with both payloads stored whole. Returns its id."""
    row = _insert_observation(conn, **kwargs)
    conn.commit()
    return row


def _is_v1(conn: sqlite3.Connection) -> bool:
    """A v1 database has observations.input; v2 has observations.in_h."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(observations)")}
    return "input" in cols and "in_h" not in cols


def migrate(conn: sqlite3.Connection) -> int:
    """Carry a v3.8.2 obs.db forward. Returns the number of rows rewritten.

    All-or-nothing: on any failure the transaction rolls back and the file is
    still a valid v1 database, so the next connect retries from the top. Rows
    stream out of the renamed source table rather than loading into memory.

    v1 payloads were stored clipped at 1024 chars, so every migrated row is
    flagged ``truncated=1``: that content is already lost and must never be
    mistaken for a complete capture.
    """
    if not _is_v1(conn):
        return 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("ALTER TABLE observations RENAME TO observations_v1")
        conn.execute("DROP TABLE IF EXISTS obs_fts")
        for stmt in _DDL:
            conn.execute(stmt)
        src = conn.cursor()
        src.execute(
            "SELECT tool, input, output, project, agent, session_id, ts"
            " FROM observations_v1 ORDER BY id"
        )
        moved = 0
        for tool, inp, out, project, agent, sid, ts in src:
            _insert_observation(
                conn, tool=tool or "", input_text=inp or "", output_text=out or "",
                project=project or "", agent=agent or "", session_id=sid or "",
                ts=ts or "", truncated=True,
            )
            moved += 1
        src.close()
        conn.execute("DROP TABLE observations_v1")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return moved


def _scope_clause(scope: str, session_id: str, project: str) -> tuple[str, tuple]:
    """SQL fragment and parameters that narrow a match to one session or project.

    Scoping is an equality test on the joined observations row, not an FTS term:
    the default unicode61 tokenizer splits on '_' and '-', so any scope token
    built from a session id would silently become a multi-word phrase.
    """
    if scope == "session":
        return " AND o.session_id = ?", (session_id,)
    if scope == "project":
        return " AND o.project = ?", (project,)
    return "", ()


def _blob_text(conn: sqlite3.Connection, h: str | None) -> str:
    return (get_blob(conn, h) or b"").decode("utf-8", "replace") if h else ""


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    k: int = 5,
    scope: str = "session",
    session_id: str = "",
    project: str = "",
    snippet_tokens: int = 32,
    full: bool = False,
) -> list[dict]:
    """Full-text search over observations, scoped to one session by default.

    ``full=False`` returns an FTS5 snippet window around the hit; the caller
    asked where a term appears, and the padding around it is billed to their
    context. ``full=True`` reads the blobs and returns the whole payloads.

    The query is bound as a literal FTS phrase — operators inside it are data.
    """
    if scope not in _SCOPES:
        raise ValueError(f"scope must be one of {_SCOPES}, got {scope!r}")
    if not query.strip():
        return []
    phrase = '"' + query.replace('"', '""') + '"'
    where, scope_params = _scope_clause(scope, session_id, project)
    if full:
        cols = "o.in_h, o.out_h"
        head: tuple = ()
    else:
        # snippet() resolves the FTS table by NAME, never by the JOIN alias.
        cols = ("snippet(obs_fts, 1, '', '', '…', ?), "
                "snippet(obs_fts, 2, '', '', '…', ?)")
        head = (snippet_tokens, snippet_tokens)
    try:
        rows = conn.execute(
            "SELECT o.id, o.tool, " + cols + ", o.project, o.agent, o.ts,"
            " o.session_id, o.truncated FROM obs_fts f"
            " JOIN observations o ON o.id = f.rowid"
            " WHERE obs_fts MATCH ?" + where + " ORDER BY rank LIMIT ?",
            head + (phrase,) + scope_params + (max(1, k),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "id": r[0], "tool": r[1],
            "input": _blob_text(conn, r[2]) if full else r[2],
            "output": _blob_text(conn, r[3]) if full else r[3],
            "project": r[4], "agent": r[5], "timestamp": r[6],
            "session_id": r[7], "truncated": bool(r[8]),
        }
        for r in rows
    ]
