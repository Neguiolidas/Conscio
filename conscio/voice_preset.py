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
# warn once per process start forever. Keyed by (kind, path): one warning per
# file and per kind per process, and no repeat for what was already reported.
# Kind is part of the key because the two states say different things — a file
# that is NOT THERE is the 4.9.2 packaging bug, a file that is there but
# unusable is a different failure, and collapsing both hides which one happened.
_warned: set[tuple[str, str]] = set()


def _warn_missing(path: Path) -> None:
    """Announce a preset the install should have shipped but did not."""
    key = ("missing", str(path))
    if key in _warned:
        return
    _warned.add(key)
    log.warning(
        "voice preset not found: %s — the install ships no such file; "
        "voice injection stays off (resolve_voice_preset returns '')",
        path,
    )


def _warn_unusable(path: Path, reason: object) -> None:
    """Announce a preset that exists but cannot be used (corrupt/unreadable)."""
    key = ("unreadable", str(path))
    if key in _warned:
        return
    _warned.add(key)
    log.warning(
        "voice preset unreadable: %s — the file exists but is not a usable "
        "file (%s); voice injection stays off "
        "(resolve_voice_preset returns '')",
        path, reason,
    )


def resolve_voice_preset(name: str) -> str:
    """
    Return the preset name if its file exists; '' for 'none'/empty/missing.

    A missing preset disables injection rather than crashing — selection is
    advisory, never fatal. An empty name or 'none' is the OFF SWITCH: the user
    asked for no voice, so it stays silent. A named preset that is not there
    is an install missing data; a named preset that is there but is not a
    usable file is a different failure. Both warn, with different words, and
    neither is ever confused with the off switch.
    """
    name = (name or "").strip()
    if not name or name.lower() == "none":
        return ""
    path = PRESET_DIR / f"{name}.md"
    if path.is_file():
        return name
    if path.exists():
        _warn_unusable(path, "exists but is not a regular file")
    else:
        _warn_missing(path)
    return ""


def available_presets() -> list[str]:
    """List installed preset names (file stems) in conscio/presets/voice/."""
    if not PRESET_DIR.is_dir():
        return []
    return sorted(p.stem for p in PRESET_DIR.glob("*.md"))
