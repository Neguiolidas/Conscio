"""O funil de tres portas, montado.

Lexica -> ancora (as duas em ``classes.find_claims``) -> predicado
(``evidence.check``). Nada aqui decide contestar: esta entrega e modo sombra,
e a contestacao so liga depois do portao de corpus (C5b).

Zero imports do resto do Conscio: vendorizado para junto do hook.
"""

from __future__ import annotations

from dataclasses import dataclass

from .classes import find_claims
from .evidence import check


@dataclass(frozen=True)
class Finding:
    cls_name: str
    anchor: str
    outcome: str
    evidence: str


def recognise(conn, text: str, session_id: str) -> list[Finding]:
    """Um Finding por afirmacao ancorada. Texto sem ancora nao produz nada --
    e assim que a afirmacao vaga morre, sem regra especial contra ela."""
    out: list[Finding] = []
    for claim in find_claims(text):
        verdict, pointer = check(conn, claim, session_id)
        out.append(Finding(claim.cls_name, claim.anchor, verdict, pointer))
    return out
