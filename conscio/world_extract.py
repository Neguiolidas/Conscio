# conscio/world_extract.py
"""Deterministic world_state → entities extractor.

Pure function: no clock, no rng, no I/O, no LLM. The same `world_state`
string always yields the same entities dict — stable names are what make
reality-tracking's prev_state→state comparison meaningful.

Mirrors the producer contract in `PerceptionFrame.to_world_state()` /
`event_to_frame()`:

- ``[source]``            → frame header, not a fact — skipped
- ``key: value``          → string observation → type ``"attribute"``
- ``key=True|False``      → bool observation   → type ``"flag"``
- ``key=<number>``        → signal             → type ``"metric"``
- anything else (free text) → not a structured fact — skipped

The colon-space form is tried first, so ``=`` inside a colon-form value is
preserved verbatim (``query: a=b&c=d``). On duplicate keys the last line
wins: later observations describe the current state.
"""
from __future__ import annotations

import re

# Identifier-ish keys only ("status", "latency_ms", "db.pool-size"); a line
# whose left side contains spaces/symbols is free text, not a fact.
_KEY_RE = re.compile(r"[A-Za-z_][\w.-]*\Z")


def _classify(value: str) -> str:
    """Entity type for an ``=``-form value: flag, metric, or attribute."""
    if value in ("True", "False"):
        return "flag"
    try:
        float(value)
    except ValueError:
        return "attribute"
    return "metric"


def _parse_fact(line: str) -> tuple[str, str, str] | None:
    """``(key, state, type)`` for a structured fact line, else ``None``."""
    if ": " in line:
        key, _, value = line.partition(": ")
        etype = "attribute"
    elif "=" in line:
        key, _, value = line.partition("=")
        value = value.strip()
        etype = _classify(value)
    else:
        return None
    key = key.strip()
    if not _KEY_RE.match(key):
        return None
    return key, value.strip(), etype


def extract_entities(world_state: str) -> dict[str, dict]:
    """Extract ``{name: {type, state, attributes}}`` from a world_state string.

    Deterministic and side-effect free; unparseable lines are skipped, never
    guessed at. The value shape matches `WorldModel.add_entity`.
    """
    entities: dict[str, dict] = {}
    for raw in world_state.splitlines():
        line = raw.strip()
        if not line or (line.startswith("[") and line.endswith("]")):
            continue
        fact = _parse_fact(line)
        if fact is None:
            continue
        key, state, etype = fact
        entities[key] = {"type": etype, "state": state, "attributes": {}}
    return entities


def extract_relations(world_state: str) -> list[tuple[str, str, str]]:
    """Extract ``(source, "reports", fact_key)`` triples from a world_state.

    The ``[source]`` frame header is the only relation carrier in the
    producer contract (`PerceptionFrame.to_world_state` emits it
    unconditionally), so it is the only edge derived — no syntax is invented
    that nothing produces. A header binds every structured fact that follows
    it (until the next header) to its source; facts seen before any header
    have no edge to hang off and yield nothing. Triples are unique, in
    first-observation order, and the triple shape matches
    `WorldModel.add_relation(from_entity, relation, to_entity)`.
    """
    relations: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    source: str | None = None
    for raw in world_state.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            source = line[1:-1].strip() or None
            continue
        if source is None:
            continue
        fact = _parse_fact(line)
        if fact is None:
            continue
        triple = (source, "reports", fact[0])
        if triple not in seen:
            seen.add(triple)
            relations.append(triple)
    return relations
