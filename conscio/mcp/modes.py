"""Tool-surface modes.

A small model drowns in 35 tools; a large one is crippled by 10. The mode is a
property of the *pairing* between host and server, so it lives next to the space
(``<storage>/mcp_mode``) and outlives the process that set it.

The sets are nested — lite ⊂ balanced ⊂ ultra — so raising the mode never
removes a tool the caller was already using. ``conscio_remember`` is in all
three: a mode without a memory write is not a Conscio.

Mind the off-by-one when reading the frozensets below: they hold 9 and 17, but
they are the *pre-filter* subsets. ``conscio_mode`` is appended after the mode
filter (see ``MODE_TOOL_DEF``), so every mode serves one tool more than its set
— 10 / 18 / 35 is what a host actually lists. Ultra has no set at all; it is the
absence of a filter over the 34 entries of ``BASE_TOOL_DEFS``.
"""
from __future__ import annotations

from pathlib import Path

MODES = ("lite", "balanced", "ultra")

#: What a bare `conscio-mcp` serves. Deliberately the widest surface: an existing
#: `conscio install` user passes no --mode and must not silently lose tools.
DEFAULT_MODE = "ultra"

LITE_TOOLS = frozenset({
    "conscio_intercept",
    "conscio_recall",
    "conscio_remember",
    "conscio_advisory",
    "conscio_health",
    "conscio_note",
    "conscio_feed",
    "conscio_state",
    "conscio_events",
})

BALANCED_TOOLS = LITE_TOOLS | frozenset({
    "conscio_recall_observations",
    "conscio_kg_query",
    "conscio_wings_search",
    "conscio_handoff",
    "conscio_decide",
    "conscio_council",
    "conscio_verify",
    "conscio_context_budget",
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
