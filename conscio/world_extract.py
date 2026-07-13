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
        if ": " in line:
            key, _, value = line.partition(": ")
            etype = "attribute"
        elif "=" in line:
            key, _, value = line.partition("=")
            value = value.strip()
            etype = _classify(value)
        else:
            continue
        key = key.strip()
        if not _KEY_RE.match(key):
            continue
        entities[key] = {"type": etype, "state": value.strip(),
                         "attributes": {}}
    return entities
