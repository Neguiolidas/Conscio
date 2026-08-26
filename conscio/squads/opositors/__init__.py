# conscio/squads/opositors/__init__.py
"""Opositors squad — hostile pressure to validate premises (v4.4).

Voices:
- caustic          : acidic visual/UX critique
- devils_advocate  : (Ato 4) argues opposite position
- skeptic_engineer : (Ato 4) hunts over-engineering
- douche_reviewer  : passive-aggressive code review
"""
from __future__ import annotations

from conscio.squads._router import register_voice

__all__ = ["load_voices"]


def load_voices() -> None:
    """Register all Opositors voices in the registry (idempotent)."""
    from conscio.squads.opositors.caustic import CausticVoice
    from conscio.squads.opositors.devils_advocate import DevilsAdvocateVoice
    from conscio.squads.opositors.douche_reviewer import DoucheReviewerVoice
    from conscio.squads.opositors.skeptic_engineer import SkepticEngineerVoice

    register_voice("caustic", CausticVoice(), squad="opositors")
    register_voice("douche_reviewer", DoucheReviewerVoice(), squad="opositors")
    register_voice("devils_advocate", DevilsAdvocateVoice(), squad="opositors")
    register_voice("skeptic_engineer", SkepticEngineerVoice(), squad="opositors")