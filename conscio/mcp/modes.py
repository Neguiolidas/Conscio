"""Tool-surface modes.

A small model drowns in 37 tools; a large one is crippled by 10. The mode is a
property of the *pairing* between host and server, so it lives next to the space
(``<storage>/mcp_mode``) and outlives the process that set it.

The sets are nested — lite ⊂ balanced ⊂ high ⊂ ultra — so raising the mode never
removes a tool the caller was already using. ``conscio_remember`` is in all
four: a mode without a memory write is not a Conscio.

Mind the off-by-one when reading the frozensets below: they hold 9, 17, and 19,
but they are the *pre-filter* subsets. ``conscio_mode`` is appended after the mode
filter (see ``MODE_TOOL_DEF``), so every mode serves one tool more than its set.
Ultra has no set at all; it is the absence of a filter over the entries of
``BASE_TOOL_DEFS``.

Relay (4 tools) and review/act (3 tools) are conditionally appended at startup
when their respective features are configured — they are not part of any mode set.
"""
from __future__ import annotations

from pathlib import Path

MODES = ("lite", "balanced", "high", "ultra")

#: The mode a FRESH install serves on its very first boot. Deliberately the
#: narrow-but-useful surface: a brand-new host should not drown in 37 tools.
#: Once the space has a persisted ``mcp_mode`` (written on first boot below),
#: that persisted value wins forever — so an UPDATE never downgrades an
#: existing install's mode, and a fresh install starts balanced.
DEFAULT_MODE = "balanced"

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
    "conscio_cognitive_cycle",
})

HIGH_TOOLS = BALANCED_TOOLS | frozenset({
    "conscio_squad_experts",
    "conscio_squad_opositors",
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


def resolve_mode(storage, cli_mode: str | None, *, persist_first_boot: bool = True) -> str:
    """Resolve the active mode, distinguishing FRESH install from UPDATE.

    Precedence (highest first):
      1. A mode already persisted in the space (``mcp_mode``) — an existing
         install keeps its choice across updates, so an upgrade NEVER
         downgrades a host that had already settled on a surface.
      2. An explicit ``--mode`` on the command line.
      3. Otherwise, the space's age decides the default:
         - a space with an existing identity (``instance.json``) but no
           persisted mode is a PRE-4.5.3 install that had been running on the
           historical default ``ultra`` — preserve ``ultra`` so the update does
           not silently shrink its surface.
         - a genuinely empty space is a FRESH install → ``balanced``.

    ``persist_first_boot`` (default True) writes the settled default on the first
    boot so the next run (an update) sees a persisted value and preserves it.
    The marker plus ``instance.json`` IS the install-vs-update detection: no
    identity + no mode means "never booted here" (fresh); identity + no mode
    means "pre-4.5.3 install" (preserve legacy ultra).
    """
    persisted = read_mode(storage)
    if persisted is not None:
        return persisted
    if cli_mode in MODES:
        mode = cli_mode
    else:
        mode = "ultra" if _space_preexists(storage) else DEFAULT_MODE
    if persist_first_boot:
        try:
            write_mode(storage, mode)
        except (OSError, ValueError):
            pass                       # never block startup on the marker write
    return mode


def _space_preexists(storage) -> bool:
    """True when the space already has an identity — i.e. this is NOT the first
    boot ever. Used to distinguish a pre-4.5.3 install (ran on the historical
    ``ultra`` default) from a fresh install (gets a balanced surface)."""
    try:
        return (Path(storage).expanduser() / "instance.json").exists()
    except (OSError, TypeError, ValueError):
        return False
