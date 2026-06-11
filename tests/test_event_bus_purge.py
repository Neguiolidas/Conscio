"""Tests for EventBus.purge_duplicates — all-time exact-duplicate collapse."""
from datetime import datetime, timedelta

import pytest

from conscio.event_bus import EventBus


def _insert(bus, type_, data_json, data_hash, ts, priority=5):
    """Insert a raw row bypassing the 60s dedup window (for test seeding)."""
    bus.db.execute(
        "INSERT INTO events (type, category, data, priority, data_hash, timestamp) "
        "VALUES (?, 'system', ?, ?, ?, ?)",
        (type_, data_json, priority, data_hash, ts),
    )
    bus.db.commit()


@pytest.fixture
def bus(tmp_path):
    b = EventBus(db_path=tmp_path / "ev.db")
    yield b
    b.close()


def test_purge_collapses_same_type_and_hash_keeping_newest(bus):
    base = datetime(2026, 1, 1, 12, 0, 0)
    # 3 identical (type, hash) rows at different timestamps
    for i in range(3):
        ts = (base + timedelta(minutes=i)).isoformat()
        _insert(bus, "perception", '{"x":1}', "hashA", ts)
    # 1 distinct row
    _insert(bus, "perception", '{"x":2}', "hashB", base.isoformat())

    removed = bus.purge_duplicates()

    assert removed == 2  # two of the three hashA rows removed
    rows = bus.db.execute("SELECT data_hash, timestamp FROM events ORDER BY data_hash").fetchall()
    hashes = [r["data_hash"] for r in rows]
    assert hashes == ["hashA", "hashB"]
    # the surviving hashA row is the NEWEST (12:02)
    surviving_a = [r for r in rows if r["data_hash"] == "hashA"][0]
    assert surviving_a["timestamp"] == (base + timedelta(minutes=2)).isoformat()


def test_purge_distinguishes_by_type(bus):
    ts = datetime(2026, 1, 1).isoformat()
    _insert(bus, "perception", "{}", "sameHash", ts)
    _insert(bus, "error", "{}", "sameHash", ts)  # same hash, different type → NOT a dup
    removed = bus.purge_duplicates()
    assert removed == 0
    assert bus.db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 2


def test_purge_dry_run_changes_nothing(bus):
    ts = datetime(2026, 1, 1).isoformat()
    for _ in range(4):
        _insert(bus, "perception", "{}", "h", ts)
    would = bus.purge_duplicates(dry_run=True)
    assert would == 3
    assert bus.db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 4  # untouched


def test_purge_idempotent(bus):
    ts = datetime(2026, 1, 1).isoformat()
    for _ in range(5):
        _insert(bus, "perception", "{}", "h", ts)
    assert bus.purge_duplicates() == 4
    assert bus.purge_duplicates() == 0  # second run removes nothing
    assert bus.db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 1


def test_purge_empty_store(bus):
    assert bus.purge_duplicates() == 0
