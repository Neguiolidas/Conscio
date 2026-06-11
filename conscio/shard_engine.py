# conscio/shard_engine.py
"""
Shard Engine — infers the agent's active cognitive mode from recent events.

Origin: Cognitive Shards (Noosphere-Manifold, CC BY-NC-SA 4.0). Operational
paraphrase: seven cognitive modes, inferred deterministically from the keyword
content of recent EventBus events. Advisory only — never feeds drives or goals.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class Shard(Enum):
    ARCHITECT = "ARCHITECT"
    ENGINEER = "ENGINEER"
    JANITOR = "JANITOR"
    SECURITY_ANALYST = "SECURITY_ANALYST"
    ARCHAEOLOGIST = "ARCHAEOLOGIST"
    EXPERT_CODER = "EXPERT_CODER"
    DREAMER = "DREAMER"


MIN_KEYWORD_LEN = 3
DEFAULT_WINDOW = 20

# Disjoint keyword sets — no keyword appears in two shards, so a word votes
# for exactly one shard (deterministic tallies). All keywords are whole-word
# matched, lowercase, length >= MIN_KEYWORD_LEN.
_SHARD_KEYWORDS: dict[Shard, list[str]] = {
    Shard.ARCHITECT: ["design", "architecture", "plan", "blueprint", "schema"],
    Shard.ENGINEER: ["implement", "build", "feature", "code", "develop"],
    Shard.JANITOR: ["refactor", "cleanup", "prune", "tidy", "dedupe", "lint"],
    Shard.SECURITY_ANALYST: ["bug", "vulnerability", "security", "exploit", "error", "anomaly", "cve"],
    Shard.ARCHAEOLOGIST: ["research", "investigate", "explore", "history", "origin"],
    Shard.EXPERT_CODER: ["debug", "trace", "diagnose", "fix", "stacktrace"],
    Shard.DREAMER: ["dream", "crystallize", "consolidate", "friction", "release"],
}

# Precompiled whole-word patterns (\bkw\b), guarded by MIN_KEYWORD_LEN.
_SHARD_PATTERNS: dict[Shard, list[re.Pattern]] = {
    shard: [re.compile(rf"\b{re.escape(kw)}\b") for kw in kws if len(kw) >= MIN_KEYWORD_LEN]
    for shard, kws in _SHARD_KEYWORDS.items()
}


def _event_text(event: dict) -> str:
    """
    Flatten an event into scannable lowercase text: the event `type` plus the
    recursively-collected *values* of its `data` dict. Keys are excluded — they
    are structural metadata (e.g. 'confidence') and would create spurious hits.
    """
    parts: list[str] = [str(event.get("type", ""))]

    def _flatten(v) -> None:
        if isinstance(v, dict):
            for sub in v.values():          # values only
                _flatten(sub)
        elif isinstance(v, (list, tuple)):
            for sub in v:
                _flatten(sub)
        else:
            parts.append(str(v))

    _flatten(event.get("data", {}))
    return " ".join(parts).lower()


def infer_shard(events: list[dict], window: int = DEFAULT_WINDOW) -> Optional[Shard]:
    """
    Infer the dominant cognitive shard from the most recent `window` events.

    `events` is newest-first (as EventBus.query returns): index 0 is the newest.
    Returns the Shard with the most whole-word keyword hits, or None when no
    keyword matches anywhere. Ties are broken in favor of the shard whose
    keyword appeared in the most-recent (lowest-index) event.
    """
    scores: dict[Shard, int] = {s: 0 for s in Shard}
    recency: dict[Shard, Optional[int]] = {s: None for s in Shard}

    for idx, event in enumerate(events[:window]):
        text = _event_text(event)
        for shard, patterns in _SHARD_PATTERNS.items():
            hits = sum(1 for p in patterns if p.search(text))
            if hits:
                scores[shard] += hits
                if recency[shard] is None:      # first = newest contributing event
                    recency[shard] = idx

    best: Optional[Shard] = None
    for shard in Shard:                          # stable enum order as final fallback
        if scores[shard] == 0:
            continue
        if best is None:
            best = shard
        elif scores[shard] > scores[best]:
            best = shard
        elif scores[shard] == scores[best] and recency[shard] < recency[best]:
            best = shard                         # tie -> more recent event wins
    return best


class ShardEngine:
    """
    Stateful wrapper around infer_shard. Holds the current shard in memory and
    emits a `shard:transition` event on the bus only when the shard changes.
    A None inference does not clear a known shard (advisory stability).
    """

    def __init__(self, event_bus, window: int = DEFAULT_WINDOW):
        self._bus = event_bus
        self._window = window
        self.current: Optional[Shard] = None

    def update(self, events: list[dict]) -> Optional[Shard]:
        new = infer_shard(events, self._window)
        if new is not None and new != self.current:
            self._bus.emit(
                type="system",
                category="consciousness",
                data={
                    "shard_transition": True,
                    "from": self.current.value if self.current else None,
                    "to": new.value,
                },
            )
            self.current = new
        return self.current
