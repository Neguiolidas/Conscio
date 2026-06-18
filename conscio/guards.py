"""Internal defensive helpers — durable guards for the bug classes the v1.9
hardening hunt found *recurring* (not one-off). The point is prevention: a new
module should do the safe thing by default instead of rediscovering the same bug
in v1.10 / v1.11.

Postmortem class → durable guard:
  - narrow-except / corrupt-file read (B-005, B-006, B-008) → ``safe_read_json``:
    read + parse a JSON file to a dict, returning None on ANY problem (missing,
    OSError, binary/non-UTF-8, malformed, wrong type). Never raises.
  - schema-drift / incomplete-JSON (B-011: a valid dict missing keys a newer
    version expects → KeyError on first use) → ``read_json_dict``: merge the
    loaded object over a default skeleton so every required key always exists.
  - sentinel-as-unbounded (B-004: SQLite ``LIMIT -1`` = unbounded) → ``clamp_int``.
  - tz / local-vs-UTC (B-003b, B-007) is guarded by
    ``timeutil.naive_utc_from_epoch`` + the architectural test
    ``tests/test_durable_guards.py::test_no_bare_fromtimestamp_outside_timeutil``,
    which fails CI if any module reintroduces a bare ``datetime.fromtimestamp()``.

Internal only — not exported from ``conscio/__init__`` (public API is frozen).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def safe_read_json(path: Path) -> Optional[dict]:
    """Return the JSON object at ``path`` as a dict, or None on ANY problem:
    missing file, OSError, binary / non-UTF-8 content (UnicodeDecodeError),
    malformed JSON, or valid JSON that is not an object. Never raises — so a
    corrupt sidecar can never crash construction (I-S4)."""
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_json_dict(path: Path, default: dict) -> dict:
    """Load the JSON object at ``path``, MERGED over ``default`` so every key in
    ``default`` is guaranteed present (loaded values win).

    Guards the schema-drift class (B-011): a long-lived persisted file written by
    an older version can be valid JSON yet miss a key a newer method assumes,
    which would raise KeyError on first use. Corruption / missing / non-dict all
    fall back to a copy of ``default``.

    ``default`` must be a freshly-built skeleton (its mutable values are shared
    into the result by a shallow copy); callers here build one per call.
    """
    merged = dict(default)
    data = safe_read_json(path)
    if data is not None:
        merged.update(data)
    return merged


def clamp_int(value: int, lo: int, hi: int) -> int:
    """Clamp ``value`` into the inclusive range ``[lo, hi]``. Guards the
    sentinel-as-unbounded class — e.g. a negative limit reaching SQLite
    ``LIMIT ?`` (where ``-1`` means *no limit*)."""
    return max(lo, min(hi, value))
