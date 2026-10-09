"""Máquina de estados explícita com concorrência otimista e leases sobre SQLite.

Origem:
- foreman: foreman/models.py:18-90, foreman/ledger.py:181-310
Características:
- Estados e transições rigorosamente configuráveis e validadas no construtor.
- claim_next atômico (compare-and-swap): exatamente um trabalhador por item.
- Reclamação automática de leases expiradas com teto para estado bloqueado.
- Reanimação por operador via revive_blocked.
- Guarda estrita de posse do item para operações de transição e liberação.
Stdlib apenas (sqlite3, time, json).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class TransitionError(Exception):
    """Tentativa de transição de estado não autorizada pelo mapa."""


class StateLedger:
    """Ledger transacional de estados persistido em SQLite com locks lógicos de lease."""

    def __init__(
        self,
        db_path: str | Path,
        states: set[str],
        transitions: dict[str, set[str]],
        initial: str,
        claimed_state: str = "running",
        max_retries: int = 3,
        blocked_state: str = "blocked",
    ) -> None:
        self.db_path = str(db_path)
        self.states = set(states)
        self.transitions = {k: set(v) for k, v in transitions.items()}
        self.initial = initial
        self.claimed_state = claimed_state
        self.max_retries = max_retries
        self.blocked_state = blocked_state

        if self.initial not in self.states:
            raise ValueError(f"Initial state {self.initial!r} not declared in states")
        if self.claimed_state not in self.states:
            self.states.add(self.claimed_state)
        if self.blocked_state not in self.states:
            self.states.add(self.blocked_state)

        # Validate movements required by ledger lifecycle
        if self.claimed_state not in self.transitions.get(self.initial, set()):
            raise ValueError(f"Transition {self.initial!r} -> {self.claimed_state!r} must be allowed for claim")
        if self.initial not in self.transitions.get(self.claimed_state, set()):
            raise ValueError(f"Transition {self.claimed_state!r} -> {self.initial!r} must be allowed for reclaim")
        if self.blocked_state not in self.transitions.get(self.claimed_state, set()):
            raise ValueError(f"Transition {self.claimed_state!r} -> {self.blocked_state!r} must be allowed for retry ceiling")
        if self.initial not in self.transitions.get(self.blocked_state, set()):
            raise ValueError(f"Transition {self.blocked_state!r} -> {self.initial!r} must be allowed for revive")

        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ledger_items (
                    item_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    owner TEXT,
                    lease_until REAL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_state_lease ON ledger_items (state, lease_until)")
            conn.commit()

    def add_item(self, item_id: str, payload: Any = "") -> None:
        """Insere um novo item no estado inicial."""
        now = time.time()
        payload_str = json.dumps(payload) if not isinstance(payload, str) else payload
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO ledger_items (item_id, state, owner, lease_until, attempts, payload, updated_at)
                VALUES (?, ?, NULL, NULL, 0, ?, ?)
                """,
                (item_id, self.initial, payload_str, now),
            )
            conn.commit()

    def claim_next(self, worker_id: str, lease_seconds: float) -> dict[str, Any] | None:
        """Tenta reivindicar atômica e exclusivamente o próximo item elegível no estado inicial."""
        now = time.time()
        lease_until = now + lease_seconds

        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                SELECT item_id, payload, attempts
                FROM ledger_items
                WHERE state = ? AND (owner IS NULL OR lease_until < ?)
                ORDER BY updated_at ASC
                LIMIT 1
                """,
                (self.initial, now),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None

            item_id = row["item_id"]
            new_attempts = row["attempts"] + 1

            conn.execute(
                """
                UPDATE ledger_items
                SET state = ?, owner = ?, lease_until = ?, attempts = ?, updated_at = ?
                WHERE item_id = ? AND (owner IS NULL OR lease_until < ?)
                """,
                (self.claimed_state, worker_id, lease_until, new_attempts, now, item_id, now),
            )
            conn.commit()

            return {
                "item_id": item_id,
                "state": self.claimed_state,
                "owner": worker_id,
                "attempts": new_attempts,
                "payload": row["payload"],
                "lease_until": lease_until,
            }

    def transition(self, item_id: str, to_state: str, worker_id: str) -> bool:
        """Move o item para um novo estado com validação estrita de transição e guarda de dono."""
        now = time.time()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT state, owner FROM ledger_items WHERE item_id = ?", (item_id,))
            row = cur.fetchone()
            if not row:
                conn.commit()
                return False

            current_state = row["state"]
            current_owner = row["owner"]

            if current_owner != worker_id:
                conn.commit()
                raise PermissionError(f"Worker {worker_id!r} is not owner of item {item_id!r} (owned by {current_owner!r})")

            allowed = self.transitions.get(current_state, set())
            if to_state not in allowed:
                conn.commit()
                raise TransitionError(f"Transition from {current_state!r} to {to_state!r} is not allowed")

            conn.execute(
                """
                UPDATE ledger_items
                SET state = ?, owner = NULL, lease_until = NULL, updated_at = ?
                WHERE item_id = ?
                """,
                (to_state, now, item_id),
            )
            conn.commit()
            return True

    def reclaim_expired(self, now: float | None = None) -> list[str]:
        """Recupera itens cujas leases expiraram, bloqueando os que ultrapassaram o teto de retentativas."""
        check_time = now if now is not None else time.time()
        reclaimed: list[str] = []

        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                SELECT item_id, attempts
                FROM ledger_items
                WHERE state = ? AND owner IS NOT NULL AND lease_until < ?
                """,
                (self.claimed_state, check_time),
            )
            rows = cur.fetchall()

            for r in rows:
                item_id = r["item_id"]
                attempts = r["attempts"]
                if attempts >= self.max_retries:
                    target_state = self.blocked_state
                else:
                    target_state = self.initial

                conn.execute(
                    """
                    UPDATE ledger_items
                    SET state = ?, owner = NULL, lease_until = NULL, updated_at = ?
                    WHERE item_id = ?
                    """,
                    (target_state, check_time, item_id),
                )
                reclaimed.append(item_id)

            conn.commit()

        return reclaimed

    def revive_blocked(self, item_id: str) -> bool:
        """Reanima um item bloqueado, resetando suas tentativas para o estado inicial."""
        now = time.time()
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE ledger_items
                SET state = ?, owner = NULL, lease_until = NULL, attempts = 0, updated_at = ?
                WHERE item_id = ? AND state = ?
                """,
                (self.initial, now, item_id, self.blocked_state),
            )
            affected = cur.rowcount
            conn.commit()
            return affected > 0

    def release(self, item_id: str, worker_id: str) -> None:
        """Libera posse do item voltando para o estado inicial sem registrar conclusão."""
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE ledger_items
                SET state = ?, owner = NULL, lease_until = NULL, updated_at = ?
                WHERE item_id = ? AND owner = ?
                """,
                (self.initial, now, item_id, worker_id),
            )
            conn.commit()
