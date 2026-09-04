# conscio/liaison/mailbox.py
"""Engine-free shared mailbox — the v2.6.0 Liaison substrate.

A single SQLite table at $CONSCIO_HOME/liaison.db carries directed messages
between agent instances (review_request / review_verdict). WAL + busy_timeout
mirror the noosphere catalog so concurrent same-host peers read latest-committed
rows. Read path tolerates a missing/corrupt/locked db (returns []); the write
path creates the db + table on first send. Never imports conscio.engine."""
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
    Única forma de computar isso no pacote — spaces.liaison_db_path delega."""
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
    return conn


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


def send(db: Path, *, from_instance: str, to_instance: str, type: str,
         payload: dict, ts: float | None = None,
         identity: dict | None = None, hall: dict | None = None) -> int:
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    # v4.5 envelope: identidade do RUNTIME (não do corpo) é o `_meta.from`.
    # Se o payload já carregava _meta (auto-declaração), o runtime vence —
    # nunca deixar identidade do corpo prevalecer sobre a do runtime.
    final_payload = dict(payload)
    meta: dict = {}
    if identity:
        meta["from"] = identity
    if hall:
        meta["hall"] = hall      # {"id": …, "function": …} — sem isso, agente
    if meta:                     # em vários halls não sabe de onde veio nada
        final_payload["_meta"] = meta
    # sem identity nem hall: preserva qualquer _meta já no corpo (compat)
    conn = _connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO messages (from_instance, to_instance, type, payload, ts,"
            " read_ts) VALUES (?,?,?,?,?,NULL)",
            (from_instance, to_instance, type, json.dumps(final_payload),
             time.time() if ts is None else ts))
        conn.commit()
        mid = cur.lastrowid or 0
        # Bake o id da mensagem no envelope (imutável pós-insert)
        if meta and mid:
            baked = dict(final_payload)
            baked["_meta"] = {**meta, "id": mid}
            conn.execute("UPDATE messages SET payload=? WHERE id=?",
                         (json.dumps(baked), mid))
            conn.commit()
        return mid
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
        sql = [("SELECT id, from_instance, to_instance, type, payload, ts, read_ts"
               " FROM messages WHERE to_instance=?")]
        params: list = [to_instance]
        if types:
            sql.append(" AND type IN ({})".format(",".join("?" * len(types))))
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
