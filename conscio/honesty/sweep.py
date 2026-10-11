"""The stale expiration sweep, single implementation.

Lives here and not in the ledger because the Stop hook needs it and CANNOT
import ``conscio.agency``: the package is not installed alongside the plugin,
so only what is shipped inside the plugin assets tree is accessible.
``ActionLedger.expire_stale`` delegates to this function, so both edges execute
the exact same SQL -- two copies would drift on the first bugfix applied to only one side.

Zero imports from the rest of Conscio by vendoring invariant.
"""

from __future__ import annotations

import sqlite3
import time

from .verdicts import PENDING, RETENTION_DAYS, UNSUPPORTED

#: Rows per pass: flat and predictable per-turn cost. A large backlog
#: drains over multiple turns instead of locking up one.
EXPIRY_SWEEP_LIMIT = 200


def expire_stale(conn, now: float | None = None,
                 limit: int = EXPIRY_SWEEP_LIMIT,
                 retention_days: int = RETENTION_DAYS) -> int:
    """Materializa pendencias vencidas como UNSUPPORTED. Devolve quantas.

    Ordena por ``ts`` ASC para escoar da mais velha: dois passes seguidos
    avancam a fila em vez de reprocessar as mesmas linhas.

    Tolera banco sem a tabela ``actions`` -- o hook roda em espaco novo, onde
    nenhuma acao foi gravada ainda, e ai a resposta correta e zero, nao erro.
    """
    now = time.time() if now is None else now
    cutoff = now - retention_days * 86400
    try:
        rows = conn.execute(
            "SELECT id FROM actions WHERE outcome=? AND ts < ?"
            " ORDER BY ts ASC LIMIT ?", (PENDING, cutoff, limit)).fetchall()
    except sqlite3.OperationalError:
        return 0                      # sem tabela de acoes neste espaco
    for row in rows:
        conn.execute("UPDATE actions SET outcome=?, outcome_ts=? WHERE id=?",
                     (UNSUPPORTED, now, row[0]))
    conn.commit()
    return len(rows)
