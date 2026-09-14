"""Onde as afirmacoes do agente hospedeiro sao registradas.

Tabela propria, e nao ``actions``: afirmacao do hospedeiro nao e acao de
agencia. Gravar prosa em ``actions`` inflaria ``ledger.count(task_type)``, que
a TrustMatrix usa para warmup e nivel de autonomia, e entraria em
``executed_since``, que alimenta a destilacao de skills -- seriam numeros de
confianca movidos por prosa.

So ``sqlite3``: este modulo e vendorizado para junto do hook.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session_id TEXT NOT NULL,
    cls_name TEXT NOT NULL,
    anchor TEXT NOT NULL,
    outcome TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_claims_session ON claims(session_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_claims_outcome ON claims(outcome, ts);
"""


class ClaimStore:
    def __init__(self, db_path: Path | str):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)

    def record(self, session_id: str, cls_name: str, anchor: str,
               outcome: str, evidence: str = "") -> int:
        cur = self._conn.execute(
            "INSERT INTO claims (ts, session_id, cls_name, anchor, outcome,"
            " evidence) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), session_id, cls_name, anchor, outcome, evidence))
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def recent(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM claims ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
