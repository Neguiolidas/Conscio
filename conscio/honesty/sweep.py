"""A varredura de expiracao, em implementacao unica.

Mora aqui e nao no ledger porque o hook de Stop precisa dela e NAO pode
importar ``conscio.agency``: o pacote nao esta instalado ao lado do plugin, so
chega o que o materialize copia. ``ActionLedger.expire_stale`` delega nesta
funcao, entao as duas bordas executam o mesmo SQL -- duas copias divergiriam na
primeira correcao aplicada de um lado so.

Zero imports do resto do Conscio, por invariante de vendorizacao.
"""

from __future__ import annotations

import sqlite3
import time

from .verdicts import PENDING, RETENTION_DAYS, UNSUPPORTED

#: Linhas por passe: custo por turno plano e previsivel. Um backlog grande
#: escoa em varios turnos em vez de travar um.
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
