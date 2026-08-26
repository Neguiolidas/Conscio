# conscio/squads/__init__.py
"""Squads module — v4.4 Multi-Squad system.

Orthogonal advisory squads that run alongside the Council. Each squad is a
closed namespace (own voices, own EventBus event types, own MCP tool).

Squads:
- experts:  constructive technical specialisation
  (Optimizer, Auditor, QA, Promptor — deterministic, LLM opt-in)
- opositors: hostile pressure to validate premises
  (Caustic, Devil's Advocate, Skeptic Engineer, Douche Reviewer —
   deterministic, LLM opt-in)

The Council (engine.council) is untouched and lives in conscio.gates.
"""
from __future__ import annotations

from conscio.squads._base import Voice, VoiceResult, VoiceVote
from conscio.squads._router import (
    EXPERTS_ORDER,
    EXPERTS_VOICES,
    OPOSITORS_ORDER,
    OPOSITORS_VOICES,
    get_voice,
    list_voices,
    register_voice,
)

# Eager-register squad voices so `import conscio.squads` immediately exposes
# them. Idempotent (register_voice re-registers over same name).
from conscio.squads.experts import load_voices as _load_experts

_load_experts()

__all__ = [
    "EXPERTS_ORDER",
    "EXPERTS_VOICES",
    "OPOSITORS_ORDER",
    "OPOSITORS_VOICES",
    "Voice",
    "VoiceResult",
    "VoiceVote",
    "get_voice",
    "list_voices",
    "register_voice",
]