# conscio/squads/experts/__init__.py
"""Experts squad — constructive technical specialisation (v4.4).

Voices:
- optimizer  : performance heuristics
- auditor    : security heuristics
- qa         : (Ato 2) test/fuzzing/edge-case heuristics
- promptor   : (Ato 2) prompt optimisation heuristics
"""
from __future__ import annotations

from conscio.squads._router import register_voice

__all__ = ["load_voices"]


def load_voices() -> None:
    """Register all Experts voices in the registry (idempotent)."""
    from conscio.squads.experts.auditor import AuditorVoice
    from conscio.squads.experts.optimizer import OptimizerVoice
    from conscio.squads.experts.promptor import PromptorVoice
    from conscio.squads.experts.qa import QAVoice

    register_voice("optimizer", OptimizerVoice(), squad="experts")
    register_voice("auditor", AuditorVoice(), squad="experts")
    register_voice("qa", QAVoice(), squad="experts")
    register_voice("promptor", PromptorVoice(), squad="experts")