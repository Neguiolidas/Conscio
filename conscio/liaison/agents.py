# conscio/liaison/agents.py
"""Agent presence + capabilities (v4.1.1 — Ato 2 / Pilar 1).

A deterministic SQL table over the shared `liaison.db` (no extra connection,
no LLM). Every agent registers an `agents` row keyed by `instance_id`, with
its `capabilities` (tags the routing layer can match on), `status` and a
`last_heartbeat` UPSERTed on each tick.

Pure plumbing:
- Never raises: missing/corrupt/locked db degrades to {} / False.
- Schema is additive: CREATE TABLE IF NOT EXISTS. No migration.
- Capabilities are a comma-separated string (SQLite has no array type);
  routing parses on demand. Bounded to 256 chars to keep the row small.

Discovery model:
- A row exists -> the agent is alive as of `last_heartbeat`.
- `list_agents(capability=...)` filters by tag match.
- First contact from a peer triggers `register_agent` so the row exists
  before the next routing decision.
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from .mailbox import _connect

BUSY_TIMEOUT_MS = 3000
TABLE = "agents"
MAX_CAPS_LEN = 256   # hard cap on the capabilities field
STALE_AFTER_S = 600  # 10 min — heartbeat older than this is "stale"


_DDL = (
    f"CREATE TABLE IF NOT EXISTS {TABLE} ("
    " instance_id    TEXT PRIMARY KEY,"
    " model          TEXT NOT NULL DEFAULT '',"
    " status         TEXT NOT NULL DEFAULT 'alive',"
    " capabilities   TEXT NOT NULL DEFAULT '',"
    " last_heartbeat REAL NOT NULL,"   # trailing comma abaixo pre-ALTER
    " nome           TEXT NOT NULL DEFAULT '',"
    " familia        TEXT NOT NULL DEFAULT '',"
    " runtime        TEXT NOT NULL DEFAULT '',"
    " papel          TEXT NOT NULL DEFAULT ''"
    ")"
)

# v4.5: colunas de identidade (nome/familia/runtime/papel). Registros legados
# (criados sem elas) recebem via ALTER idempotente no caminho de escrita.
_IDENTITY_COLS = ("nome", "familia", "runtime", "papel")


def _ensure_identity_columns(conn: sqlite3.Connection) -> None:
    """Migrate a legacy agents table by adding the v4.5 identity columns.

    Idempotent: checks PRAGMA table_info and only ALTERs absent columns.
    Never raises (best-effort) — a failing migration degrades to missing
    columns, and the code that reads them defaults to ''.
    """
    try:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({TABLE})")}
        for name in _IDENTITY_COLS:
            if name not in cols:
                conn.execute(
                    f"ALTER TABLE {TABLE} ADD COLUMN {name} "
                    "TEXT NOT NULL DEFAULT ''"
                )
        conn.commit()
    except sqlite3.Error:
        pass


# ── internal ──────────────────────────────────────────────────────────

def _conn(db: Path, *, read_only: bool = False) -> sqlite3.Connection | None:
    db = Path(db)
    if read_only:
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
    else:
        try:
            conn = _connect(db)
            conn.execute(_DDL)
            conn.commit()
            _ensure_identity_columns(conn)   # v4.5: migra db legado
        except sqlite3.Error:
            return None
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None
    return conn


def _normalize_caps(caps: Iterable[str]) -> str:
    """Comma-separated, deduped, lowercased, trimmed, sorted (deterministic);
    clamped to MAX_CAPS_LEN."""
    seen: set[str] = set()
    for c in caps:
        if not isinstance(c, str):
            continue
        c = c.strip().lower()
        if c and c not in seen:
            seen.add(c)
    out = ",".join(sorted(seen))
    if len(out) > MAX_CAPS_LEN:
        out = out[:MAX_CAPS_LEN].rstrip(",")
    return out


def _parse_caps(s: str) -> list[str]:
    if not s:
        return []
    return [c for c in s.split(",") if c]


# ── public API ────────────────────────────────────────────────────────

def register_agent(db: Path, *, instance_id: str, model: str = "",
                   capabilities: Iterable[str] = (),
                   status: str = "alive",
                   heartbeat: float | None = None,
                   nome: str = "", familia: str = "", runtime: str = "",
                   papel: str = "") -> bool:
    """UPSERT an agent's row (presence + capabilities + identity).

    heartbeat defaults to time.time(). Returns True on success, False on
    a missing/broken/locked db (never raises). Idempotent.

    v4.5 identity: `nome/familia/runtime/papel` são opcionais (default '').
    No upsert, identidade PRETENCHIDA no registro sobrescreve; identidade
    vazia no upsert preserva a anterior (não zera) — assim um heartbeat/
    re-registro que não passa identity não apaga o modelo/familia já vistos.
    """
    if not instance_id:
        return False
    ts = float(heartbeat if heartbeat is not None else time.time())
    conn = _conn(db)
    if conn is None:
        return False
    try:
        # Busca identidade prévia p/ preservar nos campos vazios
        prev = conn.execute(
            f"SELECT nome, familia, runtime, papel FROM {TABLE} WHERE instance_id=?",
            (instance_id,),
        ).fetchone()
        p = {c: (prev[c] if prev is not None else "")
             for c in ("nome", "familia", "runtime", "papel")}
        new_nome = nome if nome else p["nome"]
        new_fam = familia if familia else p["familia"]
        new_run = runtime if runtime else p["runtime"]
        new_papel = papel if papel else p["papel"]
        conn.execute(
            f"INSERT INTO {TABLE}(instance_id,model,status,capabilities,last_heartbeat,nome,familia,runtime,papel)"
            " VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(instance_id) DO UPDATE SET"
            "   model=excluded.model,"
            "   status=excluded.status,"
            "   capabilities=excluded.capabilities,"
            "   last_heartbeat=excluded.last_heartbeat,"
            "   nome=excluded.nome,"
            "   familia=excluded.familia,"
            "   runtime=excluded.runtime,"
            "   papel=excluded.papel",
            (instance_id, model or "", status, _normalize_caps(capabilities), ts,
             new_nome, new_fam, new_run, new_papel),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def heartbeat(db: Path, instance_id: str, *,
              capabilities: Iterable[str] | None = None,
              status: str | None = None,
              model: str | None = None) -> bool:
    """Refresh last_heartbeat (and optionally capabilities/status/model)
    for an already-registered agent. No-op (returns False) if the row
    doesn't exist yet — call register_agent first."""
    if not instance_id:
        return False
    conn = _conn(db)
    if conn is None:
        return False
    try:
        row = conn.execute(
            f"SELECT model, status, capabilities FROM {TABLE} WHERE instance_id=?",
            (instance_id,),
        ).fetchone()
        if row is None:
            return False
        ts = time.time()
        if capabilities is not None or status is not None or model is not None:
            new_model = model if model is not None else row["model"]
            new_status = status if status is not None else row["status"]
            new_caps = (_normalize_caps(capabilities)
                        if capabilities is not None else row["capabilities"])
            conn.execute(
                f"UPDATE {TABLE} SET model=?, status=?, capabilities=?, last_heartbeat=?"
                " WHERE instance_id=?",
                (new_model, new_status, new_caps, ts, instance_id),
            )
        else:
            conn.execute(
                f"UPDATE {TABLE} SET last_heartbeat=? WHERE instance_id=?",
                (ts, instance_id),
            )
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def unregister(db: Path, instance_id: str) -> bool:
    """Remove an agent's row (graceful leave). No-op if absent."""
    if not instance_id:
        return False
    conn = _conn(db)
    if conn is None:
        return False
    try:
        conn.execute(f"DELETE FROM {TABLE} WHERE instance_id=?", (instance_id,))
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def get_agent(db: Path, instance_id: str) -> dict | None:
    """A single agent's row as a plain dict, or None if absent / db bad."""
    conn = _conn(db, read_only=True)
    if conn is None:
        return None
    try:
        row = conn.execute(
            f"SELECT instance_id, model, status, capabilities, last_heartbeat,"
            f" nome, familia, runtime, papel FROM {TABLE} WHERE instance_id=?",
            (instance_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["capabilities"] = _parse_caps(d.get("capabilities", ""))
        for c in _IDENTITY_COLS:          # garante presença mesmo sem migração
            d.setdefault(c, "")
        return d
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def list_agents(db: Path, *, capability: str | None = None,
                include_stale: bool = True) -> list[dict]:
    """All agent rows. When `capability` is set, filter to those carrying
    the tag. `include_stale=False` excludes rows older than STALE_AFTER_S."""
    conn = _conn(db, read_only=True)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            f"SELECT instance_id, model, status, capabilities, last_heartbeat,"
            f" nome, familia, runtime, papel FROM {TABLE}"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: list[dict] = []
    cap = capability.strip().lower() if capability else ""
    now = time.time()
    for r in rows:
        d = dict(r)
        caps = _parse_caps(d.pop("capabilities", ""))
        d["capabilities"] = caps
        for c in _IDENTITY_COLS:
            d.setdefault(c, "")
        if not include_stale and (now - float(d.get("last_heartbeat", 0)
                                             or 0)) > STALE_AFTER_S:
            continue
        if cap and cap not in caps:
            continue
        out.append(d)
    return out


def discover(db: Path, instance_id: str) -> dict | None:
    """Convenience: read a peer row as a routing-ready dict. Returns None
    if unknown. Useful at first contact: if a peer's inbox row exists but
    no agents row, the caller should register_agent on next heartbeat."""
    return get_agent(db, instance_id)


def is_alive(db: Path, instance_id: str,
             *, stale_after: float = STALE_AFTER_S) -> bool:
    """True iff the agent's row exists and its heartbeat is recent."""
    a = get_agent(db, instance_id)
    if a is None:
        return False
    return (time.time() - float(a.get("last_heartbeat", 0) or 0)) <= stale_after
