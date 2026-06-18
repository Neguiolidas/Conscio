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


def naive_utc_from_epoch(ts: float) -> datetime:
    """Naive-UTC datetime from a Unix epoch (matches naive_utcnow()'s convention).

    datetime.fromtimestamp(ts) returns NAIVE LOCAL time; comparing that ISO string
    against the naive-UTC strings the event store persists skews any time window by
    the machine's UTC offset. Convert through UTC explicitly.
    """
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
