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

import logging
from pathlib import Path

log = logging.getLogger("conscio.voice_preset")

PRESET_DIR = Path(__file__).parent / "presets" / "voice"

# Resolution runs on every engine start, so a missing file would otherwise
# warn once per process start forever. Keyed by path: one warning per missing
# file per process, and no repeat for the ones already reported.
_warned: set[str] = set()


def _warn_missing(path: Path) -> None:
    """Announce a preset the install should have shipped but did not."""
    key = str(path)
    if key in _warned:
        return
    _warned.add(key)
    log.warning(
        "voice preset not found: %s — the install ships no such file; "
        "voice injection stays off (resolve_voice_preset returns '')",
        key,
    )


def resolve_voice_preset(name: str) -> str:
    """
    Return the preset name if its file exists; '' for 'none'/empty/missing.

    A missing preset disables injection rather than crashing — selection is
    advisory, never fatal. An empty name or 'none' is the OFF SWITCH: the user
    asked for no voice, so it stays silent. A named preset whose file is gone
    is the opposite case — an install missing data — and that warns.
    """
    name = (name or "").strip()
    if not name or name.lower() == "none":
        return ""
    path = PRESET_DIR / f"{name}.md"
    if path.is_file():
        return name
    _warn_missing(path)
    return ""


def available_presets() -> list[str]:
    """List installed preset names (file stems) in conscio/presets/voice/."""
    if not PRESET_DIR.is_dir():
        return []
    return sorted(p.stem for p in PRESET_DIR.glob("*.md"))
