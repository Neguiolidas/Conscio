# conscio/squads/_router.py
"""Voice registry and lookup for Conscio squads (v4.4).

A registry maps voice names to Voice instances. Voices are grouped by
squad (experts / opositors). The router is a plain in-process dict —
no persistence, no network. Squad membership is used by the MCP tool
surface to decide which voices a host may call.
"""
from __future__ import annotations

from conscio.squads._base import Voice

_VOICE_REGISTRY: dict[str, Voice] = {}

#: Voices grouped by squad. Memberships drive MCP surface filtering.
EXPERTS_VOICES: set[str] = set()
OPOSITORS_VOICES: set[str] = set()

#: Explicit ordering used by squad convene() (recommendation roll-up).
EXPERTS_ORDER: list[str] = []
OPOSITORS_ORDER: list[str] = []


def register_voice(name: str, voice: Voice, *, squad: str) -> None:
    """Register a voice instance under ``name`` in the given squad.

    squad must be 'experts' or 'opositors'. Unknown squad names are
    rejected so a typo can't silently drop a voice from every surface.
    """
    if squad not in ("experts", "opositors"):
        raise ValueError(
            f"Unknown squad '{squad}'. Must be 'experts' or 'opositors'."
        )
    _VOICE_REGISTRY[name] = voice
    group = EXPERTS_VOICES if squad == "experts" else OPOSITORS_VOICES
    order = EXPERTS_ORDER if squad == "experts" else OPOSITORS_ORDER
    group.add(name)
    if name not in order:
        order.append(name)


def get_voice(name: str) -> Voice | None:
    """Return the Voice instance for ``name``, or None if unknown."""
    return _VOICE_REGISTRY.get(name)


def list_voices(squad: str | None = None) -> list[dict[str, str]]:
    """List voice metadata, optionally filtered by squad."""
    if squad == "experts":
        names = list(EXPERTS_ORDER)
    elif squad == "opositors":
        names = list(OPOSITORS_ORDER)
    else:
        order: list[str] = []
        # Union of both orders, preserving insertion; no duplicates.
        for n in EXPERTS_ORDER + OPOSITORS_ORDER:
            if n not in order:
                order.append(n)
        names = order
    return [_VOICE_REGISTRY[n].to_dict() for n in names if n in _VOICE_REGISTRY]