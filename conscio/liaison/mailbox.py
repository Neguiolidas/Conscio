# conscio/liaison/mailbox.py
"""Engine-free mailbox — the v2.6.0 Liaison substrate.

A single SQLite table carries directed messages between agent instances
(review_request / review_verdict / relay). Since v4.5.4 the db is PRIVATE to
one agent (``db_in_space``): agents exchange messages through the relay spool,
never by writing into each other's db. WAL + busy_timeout mirror the noosphere
catalog. Read path tolerates a missing/corrupt/locked db (returns []); the
write path creates the db + table on first send. Never imports conscio.engine."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ..sqlite_tuning import tune

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
-- v4.5: payload que não parseia como JSON mora aqui (quarentena), em vez de
-- derrubar a leitura. O row original é preservado em payload_raw p/ auditoria.
CREATE TABLE IF NOT EXISTS quarantine (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_row  INTEGER NOT NULL,
    motivo      TEXT NOT NULL,
    payload_raw TEXT,
    ts          REAL NOT NULL
);
"""


def default_db() -> Path:
    from ..noosphere.paths import conscio_home  # pure leaf; not the engine
    return conscio_home() / "liaison.db"


def db_in_space(storage: Path) -> Path:
    """Caminho canônico do liaison.db privado de um espaço (v4.5.4 C1/A5).
    Única forma de computar isso no pacote: o instalador e o servidor
    chamam esta função, e não há um segundo acessor para divergir dela."""
    return Path(storage) / "liaison.db"


def migrate_legacy(new_db: Path, legacy_db: Path, self_id: str) -> int:
    """Importa do db compartilhado só as linhas deste agente. Idempotente:
    no-op se new_db já existe. A CHECAGEM DE EXISTÊNCIA VEM ANTES DE QUALQUER
    CONEXÃO (I4) — _connect cria o arquivo e a migração nunca mais dispararia.

    A própria existência do novo db é a marca de "já migrei": nenhuma linha de
    estado extra, nenhuma tabela de outro módulo escrita daqui.
    """
    new_db = Path(new_db)
    if new_db.exists() or not Path(legacy_db).exists():
        return 0
    try:
        src = _connect(legacy_db)
    except sqlite3.Error:
        return 0
    try:
        rows = src.execute(
            "SELECT from_instance, to_instance, type, payload, ts, read_ts"
            " FROM messages WHERE to_instance=? OR from_instance=?",
            (self_id, self_id)).fetchall()
    except sqlite3.Error:
        return 0
    finally:
        src.close()
    if not rows:
        return 0
    new_db.parent.mkdir(parents=True, exist_ok=True)
    dst = _connect(new_db)
    try:
        dst.executemany(
            "INSERT INTO messages (from_instance, to_instance, type, payload,"
            " ts, read_ts) VALUES (?,?,?,?,?,?)",
            [tuple(r) for r in rows])
        dst.commit()
    finally:
        dst.close()
    return len(rows)


def resolve_db(storage: Path, explicit: str | Path | None = None, *,
               self_id: str = "") -> Path:
    """Onde ESTE agente lê e escreve mensagens (v4.5.4 C1).

    Um só resolvedor para servidor MCP, daemon e observatory: três resoluções
    independentes é como um lado passa a escrever num db que o outro não lê.
    A migração do legado vai junto porque só quem resolve o caminho sabe que
    o db novo acabou de nascer — e ela é anunciada, nunca silenciosa (R1).
    """
    if explicit:
        return Path(explicit).expanduser()
    db = db_in_space(Path(storage))
    if self_id:
        moved = migrate_legacy(db, default_db(), self_id)
        if moved:
            import sys
            print(f"[conscio] liaison: migrated {moved} legacy rows to {db}",
                  file=sys.stderr)
    return db


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    tune(conn, durable=True)
    conn.executescript(_SCHEMA)
    _ensure_spool_column(conn)
    return conn


def _ensure_spool_column(conn: sqlite3.Connection) -> None:
    """Additive migration for dbs created before v4.5.4. The index lives here
    and NOT in _SCHEMA: on an old db the schema script would try to index a
    column that does not exist yet and the whole connection would fail."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    if "spool_id" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN spool_id TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_spool_id"
                 " ON messages(spool_id) WHERE spool_id IS NOT NULL")
    # v4.5.4: a spool_id is minted per deposit, so replaying the same POST at
    # the bridge produced a second file and a second row. The sender's own row
    # id, baked into the envelope, is stable across replays and network
    # retries: (sender, their id) may land at most once. UNIQUE and not a
    # check-then-insert because the reactor and the MCP server ingest the same
    # spool concurrently. The id is extracted in Python and stored in a plain
    # column: indexing json_extract(payload) instead would make every write of
    # a malformed payload raise, and those are meant to reach quarantine.
    if "origin_id" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN origin_id TEXT")
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_origin"
                     " ON messages(from_instance, origin_id)"
                     " WHERE origin_id IS NOT NULL")
    except sqlite3.Error:
        pass          # pre-existing duplicates on an old db: leave unindexed


def _clamp(n: int) -> int:
    return max(1, min(n, 200))


def quarantine(db: Path, *, source_row: int, motivo: str,
               payload_raw: str | None = None) -> bool:
    """Park a malformed message row so it can't stall the inbox. Best-effort
    (never raises); a failing quarantine write does NOT propagate — the row is
    just skipped, same as before, so a broken db never becomes a write-path
    crash."""
    db = Path(db)
    try:
        conn = _connect(db)
        try:
            conn.execute(
                "INSERT INTO quarantine (source_row, motivo, payload_raw, ts)"
                " VALUES (?,?,?,?)",
                (source_row, motivo, payload_raw, time.time()))
            conn.commit()
            return True
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def list_quarantine(db: Path, *, limit: int = 100) -> list[dict]:
    """All quarantined rows (newest first). Missing/corrupt db -> []."""
    db = Path(db)
    if not db.exists():
        return []
    try:
        conn = _connect(db)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT id, source_row, motivo, payload_raw, ts"
            " FROM quarantine ORDER BY id DESC LIMIT ?",
            [_clamp(limit)]).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def purge_quarantine(db: Path, *, older_than_days: float = 7.0) -> int:
    """Delete quarantined rows older than the cutoff. Missing/corrupt db -> 0."""
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
            "DELETE FROM quarantine WHERE ts < ?", (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def with_envelope(payload: dict, identity: dict | None = None,
                  hall: dict | None = None) -> dict:
    """v4.5 envelope: the RUNTIME identity (not the body) is ``_meta.from``.

    If the payload already carried a self-declared _meta, the runtime wins —
    body identity must never outrank runtime identity. With neither identity
    nor hall, any _meta already in the body is preserved (compat).

    Shared with the wire on purpose (v4.5.4): a message delivered through the
    spool must carry the same envelope as one written locally, otherwise the
    recipient cannot tell who spoke or from which hall.
    """
    final_payload = dict(payload)
    meta: dict = {}
    if identity:
        meta["from"] = identity
    if hall:
        meta["hall"] = hall      # {"id": …, "function": …}: without it, an
    if meta:                     # agent in many halls can't tell the origin
        final_payload["_meta"] = meta
    return final_payload


def send(db: Path, *, from_instance: str, to_instance: str, type: str,
         payload: dict, ts: float | None = None,
         identity: dict | None = None, hall: dict | None = None) -> int:
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    final_payload = with_envelope(payload, identity, hall)
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO messages (from_instance, to_instance, type, payload, ts,"
            " read_ts) VALUES (?,?,?,?,?,NULL)",
            (from_instance, to_instance, type, json.dumps(final_payload),
             time.time() if ts is None else ts))
        conn.commit()
        mid = cur.lastrowid or 0
        # Bake the message id into the envelope (immutable after insert)
        meta = final_payload.get("_meta")
        if isinstance(meta, dict) and mid:
            baked = dict(final_payload)
            baked["_meta"] = {**meta, "id": mid}
            conn.execute("UPDATE messages SET payload=? WHERE id=?",
                         (json.dumps(baked), mid))
            conn.commit()
        return mid
    finally:
        conn.close()


def _origin_id(payload) -> str | None:
    """The sender's own message id, when the envelope carries one.

    None (never a fabricated value) for legacy or hand-made payloads: the
    unique index skips NULLs, so those keep the pre-v4.5.4 behaviour instead
    of being deduped against each other.
    """
    if not isinstance(payload, dict):
        return None
    meta = payload.get("_meta")
    if not isinstance(meta, dict):
        return None
    mid = meta.get("id")
    return str(mid) if isinstance(mid, (int, str)) and str(mid) else None


def insert_from_spool(db: Path, *, from_instance: str, to_instance: str,
                      type: str, payload, spool_id: str) -> bool:
    """Insert a message that came from the spool. True only when the row was
    really written — the unique index on spool_id turns re-ingestion (a crash
    between INSERT and unlink) into a silent no-op.

    Separate from ``send`` on purpose: send's return contract (row id) already
    has callers and must not change shape.
    """
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO messages (from_instance, to_instance, type,"
            " payload, ts, read_ts, spool_id, origin_id)"
            " VALUES (?,?,?,?,?,NULL,?,?)",
            (from_instance, to_instance, type, json.dumps(payload),
             time.time(), spool_id, _origin_id(payload)))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def inbox(db: Path, to_instance: str, *, types: list[str] | None = None,
          unread_only: bool = True, limit: int = 50,
          since_id: int | None = None) -> list[dict]:
    db = Path(db)
    if not db.exists():
        return []
    try:
        conn = _connect(db)
    except sqlite3.Error:
        return []
    try:
        sql = [("SELECT id, from_instance, to_instance, type, payload, ts, read_ts"
               " FROM messages WHERE to_instance=?")]
        params: list = [to_instance]
        if types:
            sql.append(" AND type IN ({})".format(",".join("?" * len(types))))
            params += list(types)
        if unread_only:
            sql.append(" AND read_ts IS NULL")
        if since_id is not None:
            sql.append(" AND id > ?")
            params.append(since_id)
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
            # v4.5: nunca deixar payload malformado bloquear a fila —
            # quarentena a mensagem e segue (gargalo #2 do relay).
            quarantine(db, source_row=int(r["id"]), motivo="payload_nao_json",
                       payload_raw=str(r["payload"]))
            continue
        out.append(d)
    return out


def thread(db: Path, a: str, b: str, *, limit: int = 20) -> list[dict]:
    """Last-N messages exchanged between instances a and b (BOTH directions),
    returned chronologically (oldest-first). Pure read; missing/corrupt/locked
    db -> []. payload JSON-parsed like inbox(); unparseable rows skipped. Never
    imports conscio.engine. Additive (v2.8.2): send/inbox/mark_read/purge_read
    unchanged; no schema change."""
    db = Path(db)
    if not db.exists():
        return []
    try:
        conn = _connect(db)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT id, from_instance, to_instance, type, payload, ts, read_ts"
            " FROM messages WHERE (from_instance=? AND to_instance=?)"
            " OR (from_instance=? AND to_instance=?)"
            " ORDER BY ts DESC, id DESC LIMIT ?",
            (a, b, b, a, _clamp(limit))).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: list[dict] = []
    for r in reversed(rows):              # newest-first query -> chronological
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"])
        except (TypeError, ValueError):
            quarantine(db, source_row=int(r["id"]), motivo="payload_nao_json",
                       payload_raw=str(r["payload"]))
            continue
        out.append(d)
    return out


def last_broadcast_ts(db: Path, from_instance: str) -> float | None:
    """ts of the newest message sent by `from_instance` whose payload carries a
    truthy `broadcast` flag, else None. Pure read; missing/corrupt/locked db ->
    None; rows whose payload won't parse are skipped (mirrors inbox/thread). Backs
    the proactive broadcast outstanding-guard (v2.10.0). Additive: send/inbox/
    thread/mark_read/purge_read unchanged; no schema change."""
    db = Path(db)
    if not db.exists():
        return None
    try:
        conn = _connect(db)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            # LIKE prefilter: without it a broadcast-free mailbox is fully
            # scanned+JSON-parsed on every initiate cycle. May over-match
            # (any payload containing '"broadcast"'); the parse below decides.
            "SELECT ts, payload FROM messages WHERE from_instance=?"
            " AND payload LIKE '%\"broadcast\"%'"
            " ORDER BY ts DESC, id DESC", (from_instance,)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    for r in rows:
        d = dict(r)
        try:
            payload = json.loads(d["payload"])
        except (TypeError, ValueError):
            continue                          # unparseable row -> skip
        if isinstance(payload, dict) and payload.get("broadcast"):
            return float(d["ts"])
    return None


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
            "UPDATE messages SET read_ts=? WHERE read_ts IS NULL AND id IN ({})".format(",".join("?" * len(ids))), [ts, *ids])
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
