"""
naive_utcnow() — drop-in replacement for the deprecated datetime.utcnow().

Deliberately returns a NAIVE datetime: every timestamp Conscio persists is
an ISO string compared lexicographically in SQLite; the aware form
(datetime.now(UTC)) appends '+00:00' and would interleave two incompatible
string formats with rows already on disk.
"""
from __future__ import annotations

from datetime import datetime, timezone


def naive_utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
