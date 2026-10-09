"""Prune citations of retracted facts (v4.9 item 13; hindsight port, MIT).

Docs destilados guardam ``based_on`` (ids dos fatos citados); fatos
invalidados/deletados somem do banco sem deixar rastro — "retração é
ausência de linha: nenhum watermark dispara". Puro: dict transforms,
sem DB nem LLM.

Grounding inteiramente ausente (``unresolvable``) NÃO é retração — é
link quebrado, não evidência perdida — e o doc fica em ``valid`` com
os ids pendentes intactos (eles são a única pista do que re-groundar).
``fact_types`` é allowlist por padrão: um id que não sabemos resolver
nunca vira "retracted".
"""
from __future__ import annotations

from typing import Any

DEFAULT_FACT_TYPES = frozenset({"world", "experience", "observation"})


def _cited_ids(doc: dict[str, Any]) -> set[str]:
    """Every id in the doc's ``based_on`` (missing key = nothing cited)."""
    return {str(i) for i in (doc.get("based_on") or [])}


def _unresolvable(ids: set[str], fact_types: frozenset) -> set[str]:
    """Ids whose prefix does not name a known fact type (broken links)."""
    return {i for i in ids if i.split(":", 1)[0] not in fact_types}


def partition_retracted(
    docs: list[dict[str, Any]],
    live_fact_ids: set[str],
    fact_types: frozenset = DEFAULT_FACT_TYPES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split docs into (valid, retracted).

    A doc goes to ``retracted`` when at least one cited fact of a known
    type disappeared from ``live_fact_ids``. Docs whose every citation is
    unresolvable (unknown type) stay valid — absence of proof is not
    retraction (N3).
    """
    valid, retracted = [], []
    for doc in docs:
        ids = _cited_ids(doc)
        resolvable = ids - _unresolvable(ids, fact_types)
        if resolvable and not resolvable.issubset(live_fact_ids):
            retracted.append(doc)
        else:
            valid.append(doc)
    return valid, retracted


def prune_based_on(
    doc: dict[str, Any],
    live_fact_ids: set[str],
    fact_types: frozenset = DEFAULT_FACT_TYPES,
) -> dict[str, Any]:
    """A copy of the doc without the retracted ids in ``based_on``.

    The doc itself is never mutated; unresolvable ids stay intact (they
    are the only evidence of what to re-ground).
    """
    ids = _cited_ids(doc)
    pruned = {i for i in ids
              if i in live_fact_ids or i in _unresolvable(ids, fact_types)}
    out = dict(doc)
    if pruned != ids:
        out["based_on"] = sorted(pruned)
    return out
