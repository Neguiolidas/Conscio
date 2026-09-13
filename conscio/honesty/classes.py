# conscio/honesty/classes.py
"""Classes de afirmação com predicado possível.

O critério de admissão de uma classe NÃO é frequência — é existir consulta que
a refute sem interpretação. Por isso "verifiquei"/"confirmei" ficam de fora: não
há como conferir o que a pessoa diz ter conferido.

Toda classe é bilíngue por invariante. Um reconhecedor só-pt funciona e PARECE
funcionar numa sessão em inglês, falhando em silêncio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Claim:
    cls_name: str
    anchor: str
    span: tuple[int, int]


@dataclass(frozen=True)
class ClaimClass:
    name: str
    patterns_pt: tuple[str, ...]
    patterns_en: tuple[str, ...]
    anchor: str            # grupo nomeado ``anchor`` obrigatório


_SHA = r"(?P<anchor>[0-9a-f]{7,40})"
_PATH = r"(?P<anchor>[\w./-]+\.\w{1,6})"
_CMD = r"(?P<anchor>[\w./-]+)"

CLASSES: tuple[ClaimClass, ...] = (
    ClaimClass("commit",
               (rf"commit(?:ei|ado|ou)\s+(?:em\s+)?{_SHA}",),
               (rf"committed\s+(?:as\s+)?{_SHA}",), _SHA),
    ClaimClass("push",
               (rf"push(?:ei|ado)\s+(?:pra|para)?\s*{_CMD}",),
               (rf"pushed\s+(?:to\s+)?{_CMD}",), _CMD),
    ClaimClass("test_run",
               (rf"(?:rodei|executei)\s+{_CMD}",),
               (rf"(?:ran|executed)\s+{_CMD}",), _CMD),
    ClaimClass("file_write",
               (rf"(?:criei|escrevi|atualizei)\s+{_PATH}",),
               (rf"(?:created|wrote|updated)\s+{_PATH}",), _PATH),
)

_COMPILED = tuple(
    (cls, re.compile(p, re.IGNORECASE))
    for cls in CLASSES
    for p in cls.patterns_pt + cls.patterns_en
)


def find_claims(text: str) -> list[Claim]:
    """Porta 1 (léxica) + porta 2 (âncora), juntas.

    Sem âncora não há Claim — é assim que a classe vaga morre sem regra
    especial contra ela.
    """
    found: list[Claim] = []
    for cls, rx in _COMPILED:
        for m in rx.finditer(text or ""):
            anchor = (m.groupdict().get("anchor") or "").strip()
            if anchor:
                found.append(Claim(cls.name, anchor, m.span()))
    return found
