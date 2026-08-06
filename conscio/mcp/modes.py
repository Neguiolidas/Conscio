"""Tool-surface modes.

A small model drowns in 34 tools; a large one is crippled by 9. The mode is a
property of the *pairing* between host and server, so it lives next to the space
(``<storage>/mcp_mode``) and outlives the process that set it.

The sets are nested — lite ⊂ balanced ⊂ ultra — so raising the mode never
removes a tool the caller was already using. ``conscio.remember`` is in all
three: a mode without a memory write is not a Conscio.
"""
from __future__ import annotations

from pathlib import Path

MODES = ("lite", "balanced", "ultra")

#: What a bare `conscio-mcp` serves. Deliberately the widest surface: an existing
#: `conscio install` user passes no --mode and must not silently lose tools.
DEFAULT_MODE = "ultra"

LITE_TOOLS = frozenset({
    "conscio.intercept",
    "conscio.recall",
    "conscio.remember",
    "conscio.advisory",
    "conscio.health",
    "conscio.note",
    "conscio.feed",
    "conscio.state",
    "conscio.events",
})

BALANCED_TOOLS = LITE_TOOLS | frozenset({
    "conscio.recall_observations",
    "conscio.kg_query",
    "conscio.wings_search",
    "conscio.handoff",
    "conscio.decide",
    "conscio.council",
    "conscio.verify",
    "conscio.context_budget",
})

_FILENAME = "mcp_mode"


def mode_path(storage) -> Path:
    return Path(storage).expanduser() / _FILENAME


def read_mode(storage) -> str | None:
    """The persisted mode, or None when absent, unreadable or unknown."""
    try:
        value = mode_path(storage).read_text("utf-8").strip()
    except (OSError, TypeError, ValueError):
        return None
    return value if value in MODES else None


def write_mode(storage, mode: str) -> None:
    """Persist ``mode`` atomically. Raises ValueError on an unknown mode."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    path = mode_path(storage)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(mode, "utf-8")
    tmp.replace(path)


def resolve_mode(storage, cli_mode: str | None) -> str:
    """Persisted mode > --mode > DEFAULT_MODE."""
    return read_mode(storage) or (cli_mode if cli_mode in MODES else DEFAULT_MODE)
