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

import datetime as dt
import hashlib
import sqlite3
import time
import zlib
from pathlib import Path

SCHEMA_VERSION = 2

# The largest real tool output measured across 13k calls was ~90 KB. 1 MiB keeps
# every genuine payload whole while refusing pathological input outright.
MAX_FIELD_BYTES = 1024 * 1024

# Measured on real tool output: at the typical 2 KB the level is irrelevant
# (0.03ms at L1 vs 0.04ms at L6); it only diverges at the 1 MiB cap (9ms/32.2%
# vs 21ms/28.2%), which is rare. The default ratio is worth 12ms on a payload
# that size, and the retention cap counts compressed bytes.
_COMPRESS_LEVEL = 6

# Everyday lock patience. Short on purpose: this runs inside a per-tool-call
# hook, where blocking the agent costs more than dropping one observation.
_BUSY_TIMEOUT_MS = 2000
# Patience while a one-time migration holds the write lock — see migrate().
_MIGRATION_LOCK_WAIT_MS = 30000

_DDL = (
    ("CREATE TABLE IF NOT EXISTS blobs ("
     " h TEXT PRIMARY KEY, z BLOB NOT NULL, n INTEGER NOT NULL)"),
    ("CREATE TABLE IF NOT EXISTS observations ("
     " id INTEGER PRIMARY KEY AUTOINCREMENT,"
     " tool TEXT NOT NULL,"
     " project TEXT NOT NULL DEFAULT '',"
     " agent TEXT NOT NULL DEFAULT 'claude-code',"
     " session_id TEXT NOT NULL,"
     " ts TEXT NOT NULL,"
     " in_h TEXT, out_h TEXT,"
     " in_n INTEGER NOT NULL DEFAULT 0,"
     " out_n INTEGER NOT NULL DEFAULT 0,"
     " truncated INTEGER NOT NULL DEFAULT 0)"),
    "CREATE INDEX IF NOT EXISTS ix_obs_session ON observations(session_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS ix_obs_project ON observations(project, id DESC)",
    "CREATE INDEX IF NOT EXISTS ix_obs_blob_in ON observations(in_h)",
    "CREATE INDEX IF NOT EXISTS ix_obs_blob_out ON observations(out_h)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS obs_fts USING fts5(tool, input, output)",
)


def _enable_wal(conn: sqlite3.Connection, timeout_ms: int) -> str:
    """Put ``conn`` into WAL, retrying a contended conversion. Returns the mode.

    Converting a rollback-journal file to WAL needs an EXCLUSIVE lock, and SQLite
    will *not* run the busy handler to get it: with several connections holding
    SHARED and wanting to upgrade, waiting could deadlock, so it returns
    SQLITE_BUSY at once. Patience here has to be ours. Measured on this machine,
    eight processes first-opening a v3.8.2 store raced ~5% of the time, which is
    the upgrade path — one store, many sessions, all opening it at once.

    A store already in WAL answers on the first pass and never sleeps, so the
    steady per-tool-call open costs exactly one pragma as before.

    Giving up returns the mode we are stuck with rather than raising: the journal
    mode is a concurrency optimisation, and a store that works less well beats a
    store that will not open.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    delay = 0.005
    while True:
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() == "wal":
                return "wal"
        except sqlite3.OperationalError:
            mode = "unknown"  # another opener holds it mid-conversion
        if time.monotonic() >= deadline:
            return str(mode).lower()
        time.sleep(delay)
        delay = min(delay * 2, 0.05)


def connect(
    path: str | Path, *, busy_timeout_ms: int = _BUSY_TIMEOUT_MS
) -> sqlite3.Connection:
    """Open (creating if needed) an obs.db at ``path`` and ensure the schema.

    WAL plus ``synchronous=NORMAL``: durable across process crashes, and only the
    newest commits can roll back on power loss. Never corruption. The right trade
    for high-rate fire-and-forget telemetry.

    **Opening an up-to-date store writes nothing.** The v3.9.1 hook opens this
    file once per tool call; stamping ``user_version`` unconditionally made every
    open a writer, so a tool call queued behind whatever else held the lock —
    measured at 2s against a 60ms budget. A store already at ``SCHEMA_VERSION``
    therefore returns immediately, without DDL and without a transaction.

    The cost of that trade: a store stamped v2 whose tables were dropped
    underneath it is no longer silently rebuilt here. It surfaces at the failing
    write instead, where ``observe()`` swallows it and returns -1 — which is the
    honest outcome for a corrupt store, rather than masking it.

    ``busy_timeout_ms`` belongs to the caller: a hook on the tool path wants to
    give up in milliseconds, while the MCP server can afford to wait.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    # busy_timeout first: it is connection-local, needs no lock, and every
    # statement below is a potential waiter.
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    _enable_wal(conn, int(busy_timeout_ms))
    conn.execute("PRAGMA synchronous=NORMAL")
    if conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION:
        return conn
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


def read_observation(conn: sqlite3.Connection, oid: int) -> dict | None:
    """Return one observation by id with both payloads whole, or None.

    The inverse of ``put_observation``, and the only supported way to read a
    stored payload back: the text lives in blobs, not in a column.
    """
    row = conn.execute(
        "SELECT id, tool, in_h, out_h, project, agent, ts, session_id, truncated"
        " FROM observations WHERE id=?", (oid,)
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "tool": row[1],
        "input": _blob_text(conn, row[2]), "output": _blob_text(conn, row[3]),
        "project": row[4], "agent": row[5], "timestamp": row[6],
        "session_id": row[7], "truncated": bool(row[8]),
    }


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
    # A one-time rewrite of a large store can outlast the everyday busy_timeout,
    # and a process that waits here is one that would otherwise drop writes. The
    # longer patience applies only while migrating: a v2 file never reaches this.
    conn.execute(f"PRAGMA busy_timeout={_MIGRATION_LOCK_WAIT_MS}")
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Re-check under the write lock. Two processes can both see a v1 file and
        # queue here; without this the loser would wake up after the winner
        # committed and rename the *migrated* table out from under it.
        if not _is_v1(conn):
            conn.rollback()
            return 0
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
    finally:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return moved


def _scope_clause(scope: str, session_id: str, project: str) -> tuple[str, tuple]:
    """SQL fragment and parameters that narrow a match to one session or project.

    Scoping is an equality test on the joined observations row, not an FTS term:
    the default unicode61 tokenizer splits on '_' and '-', so any scope token
    built from a session id would silently become a multi-word phrase.

    A narrowing scope with nothing to narrow by is a caller bug: it would match
    no row and be indistinguishable from an honest miss.
    """
    if scope == "session":
        if not session_id:
            raise ValueError("scope='session' requires a non-empty session_id")
        return " AND o.session_id = ?", (session_id,)
    if scope == "project":
        if not project:
            raise ValueError("scope='project' requires a non-empty project")
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


def stored_bytes(conn: sqlite3.Connection) -> int:
    """Total compressed size of every blob currently held.

    Payload bytes only — the FTS index carries the same text uncompressed plus
    its postings, so the file on disk is a multiple of this. It is the right
    number to prune against anyway: it is the only one that shrinks immediately
    on delete, where page_count keeps freed pages on the freelist until a VACUUM
    and would make the eviction loop run until the store was empty.
    """
    row = conn.execute("SELECT COALESCE(SUM(LENGTH(z)),0) FROM blobs").fetchone()
    return int(row[0])


def _delete_observations(conn: sqlite3.Connection, ids: list[int]) -> int:
    if not ids:
        return 0
    rows = [(i,) for i in ids]
    conn.executemany("DELETE FROM obs_fts WHERE rowid=?", rows)
    conn.executemany("DELETE FROM observations WHERE id=?", rows)
    return len(ids)


def _collect_orphan_blobs(conn: sqlite3.Connection) -> int:
    """Drop blobs no observation points at. Must run *after* the row deletes."""
    cur = conn.execute(
        "DELETE FROM blobs WHERE h NOT IN ("
        " SELECT in_h FROM observations WHERE in_h IS NOT NULL"
        " UNION SELECT out_h FROM observations WHERE out_h IS NOT NULL)"
    )
    return max(0, cur.rowcount or 0)


def prune(
    conn: sqlite3.Connection,
    *,
    max_age_days: int = 30,
    max_bytes: int = 2 * 1024 ** 3,
) -> dict:
    """Drop observations past the age window, then enforce the size cap.

    Order matters: observations first, orphan blobs second, so a blob shared by a
    surviving row is never collected. Runs at SessionStart, never on the hot path.

    The size cap is a guarantee, not a hint — eviction repeats oldest-first until
    the store is actually under ``max_bytes`` or empty. Blobs are shared and
    compressed, so no per-row size arithmetic can predict the cut point; the loop
    evicts a twentieth of what remains per pass and re-measures, which converges
    in a couple of dozen passes from any starting size.

    ``max_bytes`` bounds stored payload bytes, not the file: see ``stored_bytes``.
    """
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max_age_days)
              ).replace(tzinfo=None).isoformat()
    expired = [r[0] for r in conn.execute(
        "SELECT id FROM observations WHERE ts < ?", (cutoff,))]
    deleted = _delete_observations(conn, expired)
    freed = _collect_orphan_blobs(conn)

    while max_bytes > 0 and stored_bytes(conn) > max_bytes:
        left = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        batch = [r[0] for r in conn.execute(
            "SELECT id FROM observations ORDER BY ts, id LIMIT ?",
            (max(1, left // 20),))]
        if not batch:
            break  # nothing left to evict; the cap is smaller than the floor
        deleted += _delete_observations(conn, batch)
        freed += _collect_orphan_blobs(conn)

    conn.commit()
    return {"observations_deleted": deleted, "blobs_deleted": freed}


def last_session_id(conn: sqlite3.Connection, exclude: str = "") -> str | None:
    """Most recently written session id, or None. ``exclude`` skips one.

    Ordered by row id rather than ts: ts is supplied by the caller and a clock
    that jumps must not reorder history.
    """
    row = conn.execute(
        "SELECT session_id FROM observations WHERE session_id <> ?"
        " ORDER BY id DESC LIMIT 1", (exclude,)
    ).fetchone()
    return row[0] if row else None


def session_summary(conn: sqlite3.Connection, session_id: str) -> dict:
    """Counts for one session — the data a session-start index is built from."""
    total, first_ts, last_ts = conn.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts) FROM observations WHERE session_id=?",
        (session_id,),
    ).fetchone()
    tools = conn.execute(
        "SELECT tool, COUNT(*) c FROM observations WHERE session_id=?"
        " GROUP BY tool ORDER BY c DESC, tool ASC", (session_id,),
    ).fetchall()
    return {
        "session_id": session_id,
        "total": int(total or 0),
        "tools": [(t, int(c)) for t, c in tools],
        "first_ts": first_ts or "",
        "last_ts": last_ts or "",
    }
