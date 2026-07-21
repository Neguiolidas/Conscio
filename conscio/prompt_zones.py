"""PromptZones — two-zone prompt with cache-shape discipline (v3.1 Ato 1).

The stable zone (persona + tool schemas + system prompt) is byte-identical
across turns, enabling provider-side prompt caching at ~0.1x effective price.
The volatile zone (state + goal + memories + datetime) is reconstructed
each turn and never enters the cached prefix.

Mirrors the mechanism described in "The Harness Effect" (Writer, 2026),
Section 4.1: cache-shape discipline.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from conscio.context_manager import ConsciousnessState

ACTOR_PERSONA = (
    "You are the volition of a persistent agent. You receive the agent's "
    "current conscious state, one active goal and the available tools. "
    "Propose exactly ONE tool action that reduces the agent's dominant "
    "dissonance and advances the goal. Be conservative: prefer reading "
    "before writing, and never invent tools or arguments."
)

ACTOR_PERSONA_COMPACT = (
    "Propose ONE tool action in JSON. Reduce dissonance, advance the goal. "
    "Never invent tools."
)


@dataclass(frozen=True)
class PromptZones:
    """Two-zone prompt for cache-shape discipline.

    stable:  persona + tool schemas + system prompt (byte-stable across turns)
    volatile: state + goal + memories + datetime (reconstructed each turn)
    """
    stable: str
    volatile: str

    @property
    def full_prompt(self) -> str:
        """Join zones with a single newline — the cache breakpoint sits here."""
        return f"{self.stable}\n{self.volatile}"

    @property
    def stable_hash(self) -> str:
        """SHA-256 of stable zone — detect when cache is invalidated."""
        return hashlib.sha256(self.stable.encode()).hexdigest()[:16]


def build_zoned_prompt(
    *,
    state: ConsciousnessState,
    goal_text: str,
    catalog_text: str,
    recall_snippets: list[str] | None = None,
    few_shot: list[str] | None = None,
    intercept_enabled: bool = False,
    skill_summary: str | None = None,
    complexity: str = "full",
) -> PromptZones:
    """Build a two-zone prompt separating stable (cacheable) from volatile.

    Stable zone: ACTOR_PERSONA + tool catalog + skill summary (byte-stable).
    Volatile zone: state injection + goal + memories + few-shot + intercept.

    skill_summary (v3.1 progressive disclosure): one-line name+description
    per skill, NOT the full skill doc. Full docs loaded on invocation.

    complexity (v3.1 adaptive): 'full' (default), 'compact', or 'minimal'.
    - full:    full persona + tools + memories + few-shot + intercept
    - compact: compact persona + tools + state + goal (no memories, no few-shot)
    - minimal: tools + state + goal only (no persona — tiny models focus on schema)
    The JSON instruction block ("Respond with ONE JSON object only...")
    is injected into the stable zone so it gets cached by the provider
    instead of being re-sent on every retry. The gateway still appends
    feedback (volatile) on retries.
    """
    # Persona selection
    if complexity == "minimal":
        persona = ""
    elif complexity == "compact":
        persona = ACTOR_PERSONA_COMPACT
    else:
        persona = ACTOR_PERSONA

    stable_parts: list[str] = []
    if persona:
        stable_parts.append(persona)
        stable_parts.append("")
    if catalog_text:
        stable_parts.append("Available tools:")
        stable_parts.append(catalog_text)
    if skill_summary and complexity != "minimal":
        stable_parts.append("")
        stable_parts.append("Available skills (invoke for full instructions):")
        stable_parts.append(skill_summary)

    volatile_parts: list[str] = [state.to_injection()]
    if state.coherence_note:
        volatile_parts.append(f"Dominant dissonance: {state.coherence_note}")
    volatile_parts.append(f"Active goal: {goal_text}")
    # Memories and few-shot only for full complexity
    if complexity == "full" and recall_snippets:
        volatile_parts.append("Relevant memories:")
        volatile_parts.extend(f"- {s}" for s in recall_snippets)
    if complexity == "full" and few_shot:
        volatile_parts.append("Examples of past successful actions:")
        volatile_parts.extend(few_shot)
    if intercept_enabled and complexity != "minimal":
        volatile_parts.append("")
        volatile_parts.append(
            "## Deterministic Computation\n"
            "You may use [INTERCEPT: <expr>] tags for safe deterministic "
            "computation. The system will evaluate these before your next "
            "turn. Available: arithmetic (+, -, *, /, **, //, %), "
            "comparisons (>, <, >=, <=, ==, !=), and functions "
            "(abs, round, min, max, sum, pow, sqrt, floor, ceil, log, "
            "sin, cos, tan). Example: [INTERCEPT: 2**10] -> [RESULT: 1024]"
        )

    return PromptZones(
        stable="\n".join(stable_parts),
        volatile="\n".join(volatile_parts),
    )
