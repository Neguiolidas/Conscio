# conscio/axis_pack.py
"""
Axis-pack resolver — installable antonym-axis presets for semantic contradiction.

An axis pack is a JSON file in conscio/presets/axes/ declaring named antonym
axes (each: a positive pole and a negative pole, defined by anchor terms).
Mirrors the voice-preset architecture (voice_preset.py + presets/voice/): packs
are DATA, not code — a domain team drops legal.json or video.json into
presets/axes/ with ZERO code edits. Loading is advisory: a missing pack is
skipped, never fatal. Zero dependencies (json + pathlib + os).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("conscio.axis_pack")

AXES_DIR = Path(__file__).parent / "presets" / "axes"
DEFAULT_PACKS = ["core"]

# One warning per missing pack per process: load_axes() runs on every semantic
# construction, and a missing pack means the install lost data, not that
# nobody asked for it. Keyed by (kind, path): a pack that is NOT THERE is the
# 4.9.2 packaging bug, a pack that is there but unreadable is a different
# failure, and collapsing both into one message hides which one happened.
_warned: set[tuple[str, str]] = set()


def _warn_missing(path: Path) -> None:
    """Announce a pack the install should have shipped but did not."""
    key = ("missing", str(path))
    if key in _warned:
        return
    _warned.add(key)
    log.warning(
        "axis pack not found: %s — the install ships no such file; "
        "these axes are skipped and contradiction detection stays partial",
        path,
    )


def _warn_unusable(path: Path, reason: object) -> None:
    """Announce a pack that exists but cannot be parsed or read."""
    key = ("unreadable", str(path))
    if key in _warned:
        return
    _warned.add(key)
    log.warning(
        "axis pack unreadable: %s — the file exists but could not be loaded "
        "(%s); these axes are skipped and contradiction detection stays partial",
        path, reason,
    )


def available_axis_packs() -> list[str]:
    """List installed axis-pack names (file stems) in conscio/presets/axes/."""
    if not AXES_DIR.is_dir():
        return []
    return sorted(p.stem for p in AXES_DIR.glob("*.json"))


def resolve_axis_packs(packs: list[str] | None = None) -> list[str]:
    """Pack selection precedence: param > env (CONSCIO_AXIS_PACKS=core,legal) >
    default ['core'] — identical posture to voice-preset resolution."""
    if packs is not None:
        return list(packs)
    env = os.getenv("CONSCIO_AXIS_PACKS", "").strip()
    if env:
        return [p.strip() for p in env.split(",") if p.strip()]
    return list(DEFAULT_PACKS)


def _read_pack(name: str) -> list[dict]:
    """Return a pack's axis list, or [] if missing/unreadable (advisory)."""
    path = AXES_DIR / f"{name}.json"
    if not path.is_file():
        _warn_missing(path)
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # ValueError covers UnicodeDecodeError
        _warn_unusable(path, exc)
        return []
    axes = data.get("axes", [])
    return axes if isinstance(axes, list) else []


def load_axes(packs: list[str] | None = None) -> list[dict]:
    """Load + merge axis packs by name (additive; later packs append).

    Each returned axis: {"axis": str, "positive": [str], "negative": [str]}.
    Missing packs are skipped (advisory, never fatal).
    """
    merged: list[dict] = []
    for name in resolve_axis_packs(packs):
        merged.extend(_read_pack(name))
    return merged
