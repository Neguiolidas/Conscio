"""Tests for the shared SQLite connection tuning helper."""
import sqlite3

import pytest

from conscio.sqlite_tuning import tune

# PRAGMA synchronous reports an int, not the keyword it was set with.
_FULL = 2
_NORMAL = 1


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    yield c
    c.close()


def test_tune_sets_wal_and_normal_synchronous(conn):
    tune(conn)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == _NORMAL


def test_durable_keeps_full_synchronous(conn):
    """Safety and audit stores trade the speed back for durability.

    NORMAL can lose the most recent transactions to an OS crash or power cut.
    For memory and observability that is a fine trade; for a circuit breaker's
    trip it is not, so those callers ask for FULL explicitly. WAL is not part
    of the trade and stays either way.
    """
    tune(conn, durable=True)
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == _FULL
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_tune_leaves_the_connection_busy_timeout_alone(tmp_path):
    """The driver already supplies one, and callers tune it via connect().

    sqlite3.connect defaults to timeout=5.0 and several stores pass timeout=10.
    Setting a uniform value here would silently shorten those waits, so tune()
    must read the pragma rather than write it.
    """
    default = sqlite3.connect(str(tmp_path / "a.db"))
    explicit = sqlite3.connect(str(tmp_path / "b.db"), timeout=10)
    try:
        assert default.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        tune(default)
        assert default.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

        tune(explicit)
        assert explicit.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000
    finally:
        default.close()
        explicit.close()


def test_tune_converts_on_a_connection_that_never_waits(tmp_path):
    """timeout=0 disables waiting; WAL conversion must still be attempted.

    Guards the `or _WAL_RETRY_MS` fallback: passing a 0ms deadline to the
    retry loop would make it give up before its first sleep.
    """
    c = sqlite3.connect(str(tmp_path / "c.db"), timeout=0)
    try:
        tune(c)
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 0
    finally:
        c.close()


def test_foreign_keys_off_by_default_and_on_when_asked(tmp_path):
    """foreign_keys is per-connection and not every schema declares them."""
    a = sqlite3.connect(str(tmp_path / "a.db"))
    b = sqlite3.connect(str(tmp_path / "b.db"))
    try:
        tune(a)
        tune(b, foreign_keys=True)
        assert a.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        assert b.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        a.close()
        b.close()


def test_tune_is_idempotent(conn):
    """Reopening or re-tuning a connection must not disturb it."""
    tune(conn)
    tune(conn)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == _NORMAL


def test_tune_converts_an_existing_rollback_journal_database(tmp_path):
    """A store created before this helper still converts to WAL on open."""
    path = str(tmp_path / "legacy.db")
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE t(a)")
    legacy.commit()
    assert legacy.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    legacy.close()

    c = sqlite3.connect(path)
    try:
        tune(c)
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        c.close()
