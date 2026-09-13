# conscio/honesty/reaction.py
"""γ — a reação humana como sinal secundário.

Assimétrico por desenho: produz CONTRADICTED e JAMAIS VERIFIED. Um humano
dizendo "ok" atesta satisfação, não que o artefato mudou — e aprovação é
compatível com a ação ter falhado em silêncio, porque ele também não conferiu.

Peso zero até o portão do C5b. Medido em 188 mensagens reais: relato direto de
falha apareceu zero vez. Permanece no escopo porque a topologia é um humano
para vários agentes — ele é o único oráculo externo que a frota compartilha.
"""
from __future__ import annotations

import re

from . import verdicts as o

#: Só relato DIRETO de falha. Ambiguidade ("refaz", "de novo") fica de fora:
#: quase sempre quer dizer "faça o próximo igual", não "o que você fez quebrou".
_FAILURE = re.compile(
    r"(n[ãa]o funcionou|n[ãa]o funciona|deu erro|quebrou|ainda (est[áa]|t[áa]) "
    r"quebrad|didn'?t work|not working|still broken)", re.IGNORECASE)


def read_reaction(text: str) -> str | None:
    return o.CONTRADICTED if _FAILURE.search(text or "") else None


def attribute(pendings: list[dict]) -> int | None:
    """Só resolve com pendência ÚNICA. Cinco ações e um "não funcionou":
    atribuir a todas é errado, a uma é chute — então não atribui."""
    return pendings[0]["id"] if len(pendings) == 1 else None
