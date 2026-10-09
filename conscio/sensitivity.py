"""Sensitivity labels for knowledge content (v4.9).

Two levels, no free text:
- ``secret``: API keys, credentials, tokens — never returned by the
  default search, never injected into prompts.
- ``internal``: normal house content (the default for everything that
  was already in the store before this column existed).

The label lives on the *source* row: a leaked credential contaminates
every chunk under it, so the exclusion happens at the source level and
the search SQL stays a single NOT IN subquery.
"""
from __future__ import annotations

LEVELS = frozenset({"secret", "internal"})
DEFAULT = "internal"

# v4.9: backfill heuristic (measured over 184 sources, 0 secrets).
# A source label is ONLY auto-classified when the label names a credential
# artifact — content that is secret *by name*, before any text scan.
_SECRET_LABEL_MARKERS = (
    "credential", "password", "api_key", "apikey", "secret",
    ".env", "secrets/", "keyfile", "token",
)


def classify_label(label: str) -> str:
    """Map a source label to a sensitivity level.

    Free-text labels collapse to ``secret`` when the label itself names a
    credential artifact; anything else is ``internal``. This is the same
    backfill rule from the v4.9 audit, codified so new sources inherit it.
    """
    lowered = (label or "").lower()
    if any(marker in lowered for marker in _SECRET_LABEL_MARKERS):
        return "secret"
    return DEFAULT


def validate(level: str) -> str:
    if level not in LEVELS:
        raise ValueError(
            f"unknown sensitivity {level!r}; expected one of {sorted(LEVELS)}")
    return level
