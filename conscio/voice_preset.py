# conscio/voice_preset.py
"""
Voice preset resolver — static, installable output-style presets.

A voice preset is a markdown file in conscio/presets/voice/ encoding output-style
directives (the coherence-style preset distils Claude_Sentience by Dave Shapiro).
It is a STATIC resource: the framework surfaces only its NAME as a heartbeat
marker (⊙ voice: <name>); it emits no events, mutates no goals/drives, and runs
no engine logic. Zero dependencies — markdown read + existence check only.
"""
from __future__ import annotations

from pathlib import Path

PRESET_DIR = Path(__file__).parent / "presets" / "voice"


def resolve_voice_preset(name: str) -> str:
    """
    Return the preset name if its file exists; '' for 'none'/empty/missing.

    A missing preset disables injection rather than crashing — selection is
    advisory, never fatal.
    """
    name = (name or "").strip()
    if not name or name.lower() == "none":
        return ""
    if (PRESET_DIR / f"{name}.md").is_file():
        return name
    return ""


def available_presets() -> list[str]:
    """List installed preset names (file stems) in conscio/presets/voice/."""
    if not PRESET_DIR.is_dir():
        return []
    return sorted(p.stem for p in PRESET_DIR.glob("*.md"))
