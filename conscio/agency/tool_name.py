"""Sanitizer de nome de ferramenta (item 2 do catálogo da Jade).

O modelo às vezes emite o nome com formato impróprio (``host_health()``,
`` host_health``, ``Host_Health``) e o registry.get() cru devolvia None,
quebrando a proposta como "unknown tool" — um goal breaker fácil de evitar.

A sanitização é determinística e só normaliza o token: nunca inventa
ferramenta, nunca corrige por similaridade. Se depois de limpar o nome
continua desconhecido, o erro é o mesmo de antes.
"""
from __future__ import annotations

import re


def sanitize_tool_name(raw: object) -> str:
    """Normaliza o nome que o modelo emitiu para uma chave de registry.

    Regras (determinísticas, sem adivinhação):
    - str() se vier com tipo errado; None vira "" e falha como antes;
    - tira espaços/aspas/backticks das bordas;
    - remove parênteses de chamada colados (``ferramenta()``);
    - troca espaços internos por ``_``;
    - lower-case (o registry é todo em snake_case minúsculo).

    >>> sanitize_tool_name("host_health()")
    'host_health'
    >>> sanitize_tool_name("host_health ( )")
    'host_health'
    >>> sanitize_tool_name(" `Host_Health` ")
    'host_health'
    >>> sanitize_tool_name(None)
    ''
    """
    if not isinstance(raw, str):
        return "" if raw is None else str(raw).strip().lower()
    name = raw.strip().strip("\"'`")
    name = re.sub(r"\(\s*\)$", "", name)          # trailing call parens
    name = name.strip().strip("\"'`")              # parens may hide quotes
    name = re.sub(r"[\s()]+", "_", name)         # spaces/parens inside -> _
    name = re.sub(r"_+", "_", name)               # collapse runs of _
    return name.strip("_").lower()
