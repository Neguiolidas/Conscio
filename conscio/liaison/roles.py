# conscio/liaison/roles.py
"""Executor/orchestrator role model for the relay squad (v4.5).

Many EXECUTORS + exactly ONE ORCHESTRATOR. The orchestrator is the agent
that started the relay / the chat, and agents can hand off or seize the role
mid-conversation — assigning orchestrator to B demotes the previous
orchestrator A back to executor. Invariant enforced here: at most one row
has papel == "orchestrator".

Pure plumbing (like `agents`): engine-free, reads/writes the `agents`
registry (papel column). Never raises — missing/corrupt db degrades to
False / the executor default.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

EXECUTOR = "executor"
ORCHESTRATOR = "orchestrator"
VALID_PAPELS = {EXECUTOR, ORCHESTRATOR}


def normalize(papel: str | None) -> str:
    """Map any input to a valid papel; unknown/empty → executor.

    Accepts pt/en: orquestrador/orchestrador → orchestrator, executor → executor.
    """
    p = (papel or "").strip().lower()
    if p in ("orchestrator", "orquestrador", "orchestrador", "lider"):
        return ORCHESTRATOR
    if p in (EXECUTOR, "agente", "worker", "membro"):
        return EXECUTOR
    return EXECUTOR


def _row(db: Path, instance_id: str):
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT instance_id, papel FROM agents WHERE instance_id=?",
                (instance_id,)).fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return None


def get_role(db: Path, instance_id: str) -> str:
    """Current papel of an agent; executor if unknown/absent."""
    row = _row(db, instance_id)
    return normalize(row["papel"] if row else "")


def who_is_orchestrator(db: Path) -> str:
    """instance_id of the current orchestrator, or ''."""
    db = Path(db)
    if not db.exists():
        return ""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT instance_id FROM agents WHERE papel='orchestrator'"
                " LIMIT 1").fetchone()
            return row[0] if row else ""
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return ""


def set_role(db: Path, instance_id: str, papel: str) -> bool:
    """Set an agent's papel, enforcing the single-orchestrator invariant.

    If `papel` is orchestrator, demote any current orchestrator back to
    executor first (hand-off: new orchestrator seizes, old falls back).
    Returns False (no-op) if the agent isn't registered or db is bad.
    """
    papel = normalize(papel)
    db = Path(db)
    if not db.exists() or not instance_id:
        return False
    if _row(db, instance_id) is None:
        return False
    try:
        conn = sqlite3.connect(str(db))
        conn.execute("BEGIN IMMEDIATE")
        try:
            if papel == ORCHESTRATOR:
                conn.execute(
                    "UPDATE agents SET papel='executor' WHERE papel='orchestrator'"
                    " AND instance_id != ?", (instance_id,))
            conn.execute(
                "UPDATE agents SET papel=? WHERE instance_id=?",
                (papel, instance_id))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return False