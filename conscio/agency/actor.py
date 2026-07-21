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

# v3.1: ACTOR_PERSONA moved to prompt_zones.py, re-exported for compat
from conscio.prompt_zones import ACTOR_PERSONA  # noqa: F401


def build_actor_prompt(*, state: ConsciousnessState, goal_text: str,
                       catalog_text: str, recall_snippets: list[str] | None = None,
                       few_shot: list[str] | None = None,
                       intercept_enabled: bool = False) -> str:
    """Deprecated wrapper — returns full_prompt string for backward compat.

    v3.1: Use build_zoned_prompt() which returns PromptZones with
    cache-shape discipline. This wrapper will be removed in v3.2.
    """
    from conscio.prompt_zones import build_zoned_prompt

    pz = build_zoned_prompt(
        state=state, goal_text=goal_text, catalog_text=catalog_text,
        recall_snippets=recall_snippets or [], few_shot=few_shot or [],
        intercept_enabled=intercept_enabled,
    )
    return pz.full_prompt
