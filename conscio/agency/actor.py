# conscio/agency/actor.py
"""
Actor phase — stateless proposal prompt (spec section 5.5).

Semantic content only; the OutputGateway appends the syntax contract
(JSON or KV instructions). Zero history: every build starts from the
current ConsciousnessState, never from previous cycles.

few_shot is the v1.1 SkillLibrary hook: F1 callers pass an empty list.
"""
from __future__ import annotations

from conscio.context_manager import ConsciousnessState

ACTOR_PERSONA = (
    "You are the volition of a persistent agent. You receive the agent's "
    "current conscious state, one active goal and the available tools. "
    "Propose exactly ONE tool action that reduces the agent's dominant "
    "dissonance and advances the goal. Be conservative: prefer reading "
    "before writing, and never invent tools or arguments.")


def build_actor_prompt(*, state: ConsciousnessState, goal_text: str,
                       catalog_text: str, recall_snippets: list[str],
                       few_shot: list[str],
                       intercept_enabled: bool = False) -> str:
    sections = [ACTOR_PERSONA, "", state.to_injection()]
    if state.coherence_note:
        sections.append(f"Dominant dissonance: {state.coherence_note}")
    sections.append(f"Active goal: {goal_text}")
    if recall_snippets:
        sections.append("Relevant memories:")
        sections.extend(f"- {snippet}" for snippet in recall_snippets)
    if few_shot:
        sections.append("Examples of past successful actions:")
        sections.extend(few_shot)
    if catalog_text:
        sections.append("Available tools:")
        sections.append(catalog_text)
    if intercept_enabled:
        sections.append("")
        sections.append(
            "## Deterministic Computation\n"
            "You may use [INTERCEPT: <expr>] tags for safe deterministic "
            "computation. The system will evaluate these before your next "
            "turn. Available: arithmetic (+, -, *, /, **, //, %), "
            "comparisons (>, <, >=, <=, ==, !=), and functions "
            "(abs, round, min, max, sum, pow, sqrt, floor, ceil, log, "
            "sin, cos, tan). Example: [INTERCEPT: 2**10] -> [RESULT: 1024]"
        )
    return "\n".join(sections)
