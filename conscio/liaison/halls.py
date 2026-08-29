# conscio/liaison/halls.py
"""Agent's Hall — named groups of agents over the shared liaison mailbox (v4.5).

A Hall is a logical grouping the OWNER creates and other agents join. It resolves
the "same install, many agents" confusion: agents may share one physical
`liaison.db`, but a Hall gives each a named, owner-routed sub-context (squads).

Two tables in the same liaison.db:
  halls(hall_id, nome, dono, slug, criado_em)
  hall_members(hall_id, instance_id, papel, entrou_em)

Pure plumbing (like `agents`):
- Never raises: missing/corrupt/locked db degrades to None / [] / 0 / False.
- Engine-free: no conscio.engine import. send_to_hall uses mailbox.send only.
- fan-out to N members; a failing peer never aborts the rest.

Slug is `dono--nome` (deterministic, avoids two owners colliding on "team").
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from . import mailbox
from .mailbox import _connect

BUSY_TIMEOUT_MS = 3000
HALLS_TABLE = "halls"
MEMBERS_TABLE = "hall_members"

_HALLS_DDL = f"""
CREATE TABLE IF NOT EXISTS {HALLS_TABLE} (
    hall_id     TEXT PRIMARY KEY,
    nome        TEXT NOT NULL,
    dono        TEXT NOT NULL,
    criado_em   REAL NOT NULL
);
"""
_MEMBERS_DDL = f"""
CREATE TABLE IF NOT EXISTS {MEMBERS_TABLE} (
    hall_id     TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    papel       TEXT NOT NULL DEFAULT 'membro',
    entrou_em   REAL NOT NULL,
    PRIMARY KEY (hall_id, instance_id)
);
"""


def _slugify(nome: str) -> str:
    """ASCII, lowercase, alnum + hyphen. Non-word chars collapse to '-'.
    Returns '' for an empty/whitespace nome."""
    s = nome.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def _full_slug(dono: str, nome: str) -> str:
    """dono--nome: the owner namespaces the slug so two owners can both have
    a "team" without colliding (D6)."""
    d = _slugify(dono)
    n = _slugify(nome)
    if not n:
        return ""
    return f"{d}--{n}" if d else n


def _conn(db: Path, *, read_only: bool = False) -> sqlite3.Connection | None:
    db = Path(db)
    if read_only:
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
    else:
        try:
            conn = _connect(db)          # reuses mailbox schema bootstrap
            conn.execute(_HALLS_DDL)
            conn.execute(_MEMBERS_DDL)
            conn.commit()
        except sqlite3.Error:
            return None
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None
    return conn


# ── Halls CRUD ─────────────────────────────────────────────────────────

def create_hall(db: Path, *, dono: str, nome: str) -> dict | None:
    """Create a hall (owner-only). Returns the hall dict, or None on a dup/
    broken db. hall_id IS the slug (dono--nome, deterministic)."""
    if not dono or not nome:
        return None
    hall_id = _full_slug(dono, nome)
    if not hall_id:
        return None
    conn = _conn(db)
    if conn is None:
        return None
    try:
        dup = conn.execute(
            f"SELECT 1 FROM {HALLS_TABLE} WHERE hall_id=?", (hall_id,)).fetchone()
        if dup is not None:
            return None
        ts = time.time()
        conn.execute(
            f"INSERT INTO {HALLS_TABLE}(hall_id,nome,dono,criado_em)"
            " VALUES(?,?,?,?)", (hall_id, nome, dono, ts))
        conn.commit()
        return {"hall_id": hall_id, "nome": nome, "dono": dono,
                "criado_em": ts}
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def get_hall(db: Path, hall_id: str) -> dict | None:
    conn = _conn(db, read_only=True)
    if conn is None:
        return None
    try:
        row = conn.execute(
            f"SELECT hall_id, nome, dono, criado_em FROM {HALLS_TABLE}"
            " WHERE hall_id=?", (hall_id,)).fetchone()
        if row is None:
            return None
        return dict(row)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def list_halls(db: Path, *, dono: str | None = None) -> list[dict]:
    conn = _conn(db, read_only=True)
    if conn is None:
        return []
    try:
        if dono:
            rows = conn.execute(
                f"SELECT hall_id, nome, dono, criado_em FROM {HALLS_TABLE}"
                " WHERE dono=? ORDER BY criado_em DESC", (dono,)).fetchall()
        else:
            rows = conn.execute(
                f"SELECT hall_id, nome, dono, criado_em FROM {HALLS_TABLE}"
                " ORDER BY criado_em DESC").fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ── Membership ─────────────────────────────────────────────────────────

def add_member(db: Path, *, hall_id: str, instance_id: str,
               papel: str = "membro") -> bool:
    if not hall_id or not instance_id:
        return False
    conn = _conn(db)
    if conn is None:
        return False
    try:
        conn.execute(
            f"INSERT INTO {MEMBERS_TABLE}(hall_id,instance_id,papel,entrou_em)"
            " VALUES(?,?,?,?)"
            " ON CONFLICT(hall_id,instance_id) DO UPDATE SET"
            "   papel=excluded.papel, entrou_em=excluded.entrou_em",
            (hall_id, instance_id, papel, time.time()))
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def remove_member(db: Path, *, hall_id: str, instance_id: str) -> bool:
    if not hall_id or not instance_id:
        return False
    conn = _conn(db)
    if conn is None:
        return False
    try:
        conn.execute(f"DELETE FROM {MEMBERS_TABLE} WHERE hall_id=? AND"
                     " instance_id=?", (hall_id, instance_id))
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def is_member(db: Path, hall_id: str, instance_id: str) -> bool:
    conn = _conn(db, read_only=True)
    if conn is None:
        return False
    try:
        row = conn.execute(
            f"SELECT 1 FROM {MEMBERS_TABLE} WHERE hall_id=? AND instance_id=?",
            (hall_id, instance_id)).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def members_of(db: Path, hall_id: str, *,
               alive_only: bool = False) -> list[dict]:
    """All members of a hall. `alive_only` crosses with `agents.is_alive` when
    a registry row exists — but a member with NO registry row is still returned
    (absence of registry ≠ death; presence IS the signal, not absence)."""
    conn = _conn(db, read_only=True)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            f"SELECT hall_id, instance_id, papel, entrou_em FROM {MEMBERS_TABLE}"
            " WHERE hall_id=?", (hall_id,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        if alive_only:
            from . import agents
            # only drop when there IS a registry row that's stale: presence
            # is the liveness signal; absence stays visible (no false death).
            reg = agents.get_agent(db, d["instance_id"])
            if reg is not None and not agents.is_alive(db, d["instance_id"]):
                continue
        out.append(d)
    return out


def halls_of(db: Path, instance_id: str) -> list[dict]:
    """Halls the agent is a member of (or owns)."""
    conn = _conn(db, read_only=True)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            f"SELECT h.hall_id, h.nome, h.dono, h.criado_em"
            f" FROM {HALLS_TABLE} h JOIN {MEMBERS_TABLE} m"
            " ON h.hall_id = m.hall_id WHERE m.instance_id=?"
            " ORDER BY h.criado_em DESC", (instance_id,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ── Fan-out ───────────────────────────────────────────────────────────

def send_to_hall(db: Path, *, from_instance: str, hall_id: str, type: str,
                 payload: dict, identity: dict | None = None) -> int:
    """Fan-out a message to every member except the sender. Returns the number
    delivered (0 on resolution failure/broken db). Per-peer isolation: a failing
    member never aborts the rest."""
    if not hall_id or not from_instance:
        return 0
    members = members_of(db, hall_id, alive_only=False)
    if not members:
        return 0
    delivered = 0
    for m in members:
        target = m["instance_id"]
        if target == from_instance:
            continue
        try:
            mailbox.send(db, from_instance=from_instance, to_instance=target,
                         type=type, payload=payload, identity=identity)
            delivered += 1
        except Exception:
            continue
    return delivered