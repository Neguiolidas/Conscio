"""Shared SQLite connection tuning.

Every store in the package opens its own connection and then repeats the same
pragmas. One was missing almost everywhere, and it costs: ``synchronous``
defaults to FULL, an fsync on every commit. Measured on this machine that is
2.47ms per EventBus event against 0.08ms at NORMAL.

WAL plus NORMAL is the pairing SQLite documents as safe. A *process* crash
loses nothing — the WAL is already in the OS page cache — and only an OS crash
or power cut can cost the most recent transactions, never the database itself.
Callers holding state where losing the last write would matter (a circuit
breaker's trip, an audit ledger) pass ``durable=True`` and keep FULL. Those are
cold paths, so the speed was never theirs to win.

``busy_timeout`` is deliberately left alone. It looks unset across the package
because nothing names it, but ``sqlite3.connect`` supplies one: 5s by default,
and 10s wherever a caller passed ``timeout=10``. Overwriting that here would
silently shorten the waits those callers chose, so this reads the value instead
and reuses it as the WAL-conversion deadline.

WAL conversion is delegated to ``obsstore.enable_wal``, which already handles a
contended upgrade correctly and is the one implementation the capture hook can
also reach — obsstore imports nothing from the package, so depending on it here
cannot cycle.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .obsstore import enable_wal

# Fallback deadline for the WAL conversion retry, used only when a caller has
# explicitly disabled waiting (timeout=0). Converting is a one-off on first
# open, so a bounded wait here does not touch the steady-state path.
_WAL_RETRY_MS = 5000

# WAL size (bytes) past which tune() checkpoint-truncates the log. A bloated
# WAL (e.g. >50MB of absorbed content left 1.1GB uncommitted in the field)
# makes every later FTS5/lexical read page-scan the WAL + main DB at ~100%
# CPU. Checkpointing is cheap when small (SQLite no-ops) and reclaims disk
# + read speed when large, so tune() — the one pragma every store shares —
# runs it on open once the log exceeds this.
_WAL_CHECKPOINT_THRESHOLD = 100 * 1024 * 1024  # 100 MB


def tune(
    conn: sqlite3.Connection,
    *,
    durable: bool = False,
    foreign_keys: bool = False,
) -> None:
    """Apply the package's standard pragmas to an already-open connection."""
    busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    enable_wal(conn, busy_timeout_ms or _WAL_RETRY_MS)
    conn.execute(f"PRAGMA synchronous={'FULL' if durable else 'NORMAL'}")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys=ON")
    _checkpoint_oversized_wal(conn)


def _checkpoint_oversized_wal(conn: sqlite3.Connection) -> None:
    """Checkpoint-truncate the WAL if it has grown past the threshold."""
    try:
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    except sqlite3.OperationalError:
        return
    if mode != "wal":
        return
    try:
        db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
        size = db_path.stat().st_size
    except (sqlite3.OperationalError, OSError, TypeError):
        size = 0
    if size < _WAL_CHECKPOINT_THRESHOLD:
        return
    # Oversized: checkpoint-and-truncate so later reads do not page-scan the
    # log. A contended EXCLUSIVE lock falls back to a passive checkpoint
    # (still coalesces frames) rather than blocking the opener.
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        try:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.OperationalError:
            pass
