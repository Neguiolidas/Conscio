"""Vocabulario do desfecho de uma acao (v4.6 E1).

Tres perguntas distintas vivem na tabela ``actions`` e nao se sobrepoem:
``verdict`` e o Skeptic antes de executar, ``ok`` e o comando ter rodado, e
``outcome`` -- aqui -- e o efeito esperado ter acontecido.

A expiracao e derivada na leitura ALEM de materializada na varredura: a
varredura so roda quando alguem abre sessao, entao uma pendencia vencida
continuaria mentindo ``PENDING`` numa maquina parada por meses.
"""

from __future__ import annotations

# Fonte unica: reexportado, nunca redefinido.
from ..honesty.verdicts import (  # noqa: F401
    CONTRADICTED,
    OUT_OF_SCOPE,
    PENDING,
    RETENTION_DAYS,
    TERMINAL,
    UNSUPPORTED,
    VERIFIED,
)


def is_expired(outcome: str, ts: float, now: float,
               retention_days: int = RETENTION_DAYS) -> bool:
    """So pendencia expira. Terminal e imutavel; fora de escopo nao e pendencia."""
    if outcome != PENDING:
        return False
    return (now - ts) > retention_days * 86400


def effective(outcome: str, ts: float, now: float,
              retention_days: int = RETENTION_DAYS) -> str:
    """O desfecho como deve ser LIDO, antes de a varredura materializar."""
    if is_expired(outcome, ts, now, retention_days):
        return UNSUPPORTED
    return outcome
