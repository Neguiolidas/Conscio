"""Tests for open_board WAL journal mode retry under contention.

Verifies:
(1) Deterministic injection: retrying on 'database is locked' during
    PRAGMA journal_mode succeeds once the lock clears, yielding SCHEMA_VERSION.
(2) Always-locked: when locked persists past BUSY_TIMEOUT_MS, raises BoardBusy
    without hanging.
(3) Other OperationalError (e.g. 'disk I/O error') is not retried and raises
    immediately.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from conscio.ambient import board


def test_open_board_retries_on_wal_locked_and_succeeds(tmp_path, monkeypatch):
    """(1) Database is locked on first two PRAGMA journal_mode calls, then succeeds."""
    real_connect = sqlite3.connect
    locked_count = 0

    class _InterceptingConnection:
        def __init__(self, real_conn: sqlite3.Connection) -> None:
            self._conn = real_conn

        def execute(self, sql: str, *args, **kwargs):
            nonlocal locked_count
            if "PRAGMA journal_mode" in sql and locked_count < 2:
                locked_count += 1
                raise sqlite3.OperationalError("database is locked")
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name: str):
            return getattr(self._conn, name)

    def fake_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        return _InterceptingConnection(conn)

    monkeypatch.setattr(board.sqlite3, "connect", fake_connect)

    db_path = tmp_path / "test_board.db"
    db = board.open_board(db_path)
    try:
        assert locked_count == 2
        user_version = int(db.execute("PRAGMA user_version").fetchone()[0])
        assert user_version == board.SCHEMA_VERSION
    finally:
        db.close()


def test_open_board_always_locked_raises_board_busy(tmp_path, monkeypatch):
    """(2) Always-locked past BUSY_TIMEOUT_MS raises BoardBusy promptly."""
    monkeypatch.setattr(board, "BUSY_TIMEOUT_MS", 50)  # 50 ms timeout

    real_connect = sqlite3.connect
    locked_count = 0

    class _AlwaysLockedConnection:
        def __init__(self, real_conn: sqlite3.Connection) -> None:
            self._conn = real_conn

        def execute(self, sql: str, *args, **kwargs):
            nonlocal locked_count
            if "PRAGMA journal_mode" in sql:
                locked_count += 1
                raise sqlite3.OperationalError("database is locked")
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name: str):
            return getattr(self._conn, name)

    def fake_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        return _AlwaysLockedConnection(conn)

    monkeypatch.setattr(board.sqlite3, "connect", fake_connect)

    db_path = tmp_path / "test_busy_board.db"
    t0 = time.monotonic()
    with pytest.raises(board.BoardBusy):
        board.open_board(db_path)
    elapsed = time.monotonic() - t0

    assert locked_count >= 2
    assert 0.030 <= elapsed < 2.0  # Finished promptly, bounded well below normal 5s


def test_open_board_other_operational_error_not_retried(tmp_path, monkeypatch):
    """(3) Other OperationalError (e.g. disk I/O error) is not retried and raises."""
    real_connect = sqlite3.connect
    attempts = 0

    class _DiskErrorConnection:
        def __init__(self, real_conn: sqlite3.Connection) -> None:
            self._conn = real_conn

        def execute(self, sql: str, *args, **kwargs):
            nonlocal attempts
            if "PRAGMA journal_mode" in sql:
                attempts += 1
                raise sqlite3.OperationalError("disk I/O error")
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name: str):
            return getattr(self._conn, name)

    def fake_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        return _DiskErrorConnection(conn)

    monkeypatch.setattr(board.sqlite3, "connect", fake_connect)

    db_path = tmp_path / "test_io_error_board.db"
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        board.open_board(db_path)

    assert attempts == 1  # Exactly 1 attempt, zero retries
