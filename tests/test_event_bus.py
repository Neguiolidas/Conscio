"""
Tests for EventBus — Session event tracking with dedup.

Covers: emit, dedup, query, filters, summary, compact, stats, edge cases.
"""

import json
from datetime import timedelta

import pytest

from conscio.timeutil import naive_utcnow

from conscio.event_bus import (
    EventBus,
    Event,
    VALID_TYPES,
    VALID_CATEGORIES,
    PRIORITY_CRITICAL,
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    PRIORITY_LOW,
    PRIORITY_TRIVIAL,
    DEDUP_WINDOW_SECONDS,
)


@pytest.fixture
def bus(tmp_path):
    """Create an EventBus with a temp database."""
    db_path = tmp_path / "test_events.db"
    b = EventBus(db_path=db_path)
    yield b
    b.close()


@pytest.fixture
def populated_bus(bus):
    """Create a bus with sample events."""
    bus.emit("error", "trading", {"code": 51155, "msg": "Compliance violation"}, priority=PRIORITY_CRITICAL)
    bus.emit("perception", "system", {"cpu": 45, "disk": 89}, priority=PRIORITY_NORMAL)
    bus.emit("trade", "trading", {"action": "open_long", "symbol": "BTC-USDT"}, priority=PRIORITY_HIGH)
    bus.emit("reflection", "consciousness", {"confidence": 0.72}, priority=PRIORITY_LOW)
    bus.emit("anomaly", "system", {"disk_pct": 92, "msg": "Disk nearly full"}, priority=PRIORITY_HIGH)
    bus.emit("decision", "consciousness", {"goal": "improve_perception"}, priority=PRIORITY_NORMAL)
    return bus


# ─── Schema Tests ───────────────────────────────────────────────────────

class TestSchema:
    def test_tables_created(self, bus):
        """Events table exists after init."""
        tables = bus.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r["name"] for r in tables}
        assert "events" in table_names

    def test_indexes_created(self, bus):
        """All required indexes exist."""
        indexes = bus.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = {r["name"] for r in indexes}
        assert "idx_events_type" in index_names
        assert "idx_events_timestamp" in index_names
        assert "idx_events_category" in index_names

    def test_idempotent_init(self, bus):
        """Initializing schema twice doesn't error."""
        bus._init_schema()
        bus._init_schema()

    def test_wal_mode(self, bus):
        """Database is in WAL mode."""
        mode = bus.db.execute("PRAGMA journal_mode").fetchone()["journal_mode"]
        assert mode == "wal"

    def test_valid_types(self):
        """All documented types are in VALID_TYPES."""
        expected_subset = {"tool_call", "reflection", "trade", "error", "anomaly", "decision"}
        assert expected_subset.issubset(VALID_TYPES)

    def test_valid_categories(self):
        """All documented categories are in VALID_CATEGORIES."""
        expected = {"system", "trading", "consciousness", "external", "session"}
        assert VALID_CATEGORIES == expected


# ─── Emit Tests ─────────────────────────────────────────────────────────

class TestEmit:
    def test_basic_emit(self, bus):
        """Emitting returns a positive event ID."""
        eid = bus.emit("error", "system", {"msg": "test"})
        assert isinstance(eid, int)
        assert eid > 0

    def test_event_stored(self, bus):
        """Event data is retrievable after emit."""
        eid = bus.emit("error", "trading", {"code": 51155, "msg": "compliance"})
        event = bus.get(eid)
        assert event is not None
        assert event.type == "error"
        assert event.category == "trading"
        assert event.data["code"] == 51155
        assert event.priority == PRIORITY_NORMAL  # default

    def test_custom_priority(self, bus):
        """Custom priority is stored."""
        eid = bus.emit("error", "system", {"msg": "critical"}, priority=PRIORITY_CRITICAL)
        event = bus.get(eid)
        assert event.priority == PRIORITY_CRITICAL

    def test_invalid_type_raises(self, bus):
        """Invalid type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid event type"):
            bus.emit("invalid_type", "system", {})

    def test_invalid_category_raises(self, bus):
        """Invalid category raises ValueError."""
        with pytest.raises(ValueError, match="Invalid event category"):
            bus.emit("error", "invalid_cat", {})

    def test_project_dir(self, bus):
        """Project directory is stored."""
        eid = bus.emit("error", "system", {"msg": "test"}, project_dir="/tmp/test-conscio")
        event = bus.get(eid)
        assert event.project_dir == "/tmp/test-conscio"

    def test_attribution_confidence(self, bus):
        """Attribution confidence is stored."""
        eid = bus.emit("error", "system", {"msg": "test"}, attribution_confidence=0.85)
        event = bus.get(eid)
        assert event.attribution_confidence == 0.85

    def test_data_json(self, bus):
        """Event data is serialized as JSON."""
        data = {"key": "value", "nested": {"inner": 42}}
        eid = bus.emit("error", "system", data)
        event = bus.get(eid)
        assert event.data == data

    def test_data_hash_consistency(self, bus):
        """Same data produces same hash regardless of key order."""
        eid1 = bus.emit("error", "system", {"a": 1, "b": 2})
        # Different key order, same content
        raw_hash = bus.db.execute(
            "SELECT data_hash FROM events WHERE id = ?", (eid1,)
        ).fetchone()["data_hash"]

        import hashlib
        expected = hashlib.sha256(json.dumps({"a": 1, "b": 2}, sort_keys=True, default=str).encode()).hexdigest()
        assert raw_hash == expected


# ─── Dedup Tests ────────────────────────────────────────────────────────

class TestDedup:
    def test_dedup_same_event_within_window(self, bus):
        """Duplicate event within window returns same ID."""
        eid1 = bus.emit("error", "system", {"msg": "same error"})
        eid2 = bus.emit("error", "system", {"msg": "same error"})
        assert eid1 == eid2

    def test_different_data_not_deduped(self, bus):
        """Different data creates different events."""
        eid1 = bus.emit("error", "system", {"msg": "error A"})
        eid2 = bus.emit("error", "system", {"msg": "error B"})
        assert eid1 != eid2

    def test_different_type_not_deduped(self, bus):
        """Same data but different type creates different events."""
        eid1 = bus.emit("error", "system", {"msg": "test"})
        eid2 = bus.emit("anomaly", "system", {"msg": "test"})
        assert eid1 != eid2

    def test_dedup_window_expiry(self, bus):
        """After dedup window, same event is accepted again."""
        eid1 = bus.emit("error", "system", {"msg": "delayed dup"})

        # Manually backdate the existing event beyond the window
        old_ts = (naive_utcnow() - timedelta(seconds=DEDUP_WINDOW_SECONDS + 10)).isoformat()
        bus.db.execute("UPDATE events SET timestamp = ? WHERE id = ?", (old_ts, eid1))
        bus.db.commit()

        eid2 = bus.emit("error", "system", {"msg": "delayed dup"})
        assert eid1 != eid2  # Now it's a new event

    def test_dedup_preserves_count(self, bus):
        """Dedup doesn't inflate event count."""
        bus.emit("error", "system", {"msg": "dup test"})
        bus.emit("error", "system", {"msg": "dup test"})
        bus.emit("error", "system", {"msg": "dup test"})

        total = bus.db.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]
        assert total == 1  # Only one unique event


# ─── Batch Emit Tests ───────────────────────────────────────────────────

class TestBatchEmit:
    def test_batch_emit(self, bus):
        """Batch emit creates multiple events."""
        events = [
            {"type": "error", "category": "system", "data": {"msg": "err1"}},
            {"type": "trade", "category": "trading", "data": {"action": "buy"}},
            {"type": "perception", "category": "system", "data": {"cpu": 50}},
        ]
        ids = bus.emit_batch(events)
        assert len(ids) == 3
        assert all(isinstance(i, int) for i in ids)

    def test_batch_with_dedup(self, bus):
        """Batch emit respects dedup."""
        # Pre-emit one event
        eid1 = bus.emit("error", "system", {"msg": "pre-existing"})

        events = [
            {"type": "error", "category": "system", "data": {"msg": "pre-existing"}},  # dup
            {"type": "error", "category": "system", "data": {"msg": "new error"}},  # new
        ]
        ids = bus.emit_batch(events)
        assert ids[0] == eid1  # Same ID for duplicate
        assert ids[1] != eid1  # New ID for new event


# ─── Query Tests ────────────────────────────────────────────────────────

class TestQuery:
    def test_query_all(self, populated_bus):
        """Query with no filters returns all events."""
        events = populated_bus.query(limit=100, include_duplicates=True)
        assert len(events) == 6

    def test_query_by_type(self, populated_bus):
        """Filter by type works."""
        events = populated_bus.query(type="error")
        assert len(events) >= 1
        for e in events:
            assert e.type == "error"

    def test_query_by_category(self, populated_bus):
        """Filter by category works."""
        events = populated_bus.query(category="trading")
        assert len(events) >= 1
        for e in events:
            assert e.category == "trading"

    def test_query_by_priority(self, populated_bus):
        """Filter by max priority works."""
        events = populated_bus.query(priority_max=PRIORITY_HIGH)
        for e in events:
            assert e.priority <= PRIORITY_HIGH

    def test_query_by_since(self, populated_bus):
        """Filter by since timestamp works."""
        future = (naive_utcnow() + timedelta(hours=1)).isoformat()
        events = populated_bus.query(since=future)
        assert len(events) == 0

    def test_query_by_until(self, populated_bus):
        """Filter by until timestamp works."""
        past = (naive_utcnow() - timedelta(hours=1)).isoformat()
        events = populated_bus.query(until=past)
        assert len(events) == 0

    def test_query_by_project_dir(self, bus):
        """Filter by project_dir works."""
        bus.emit("error", "system", {"msg": "test"}, project_dir="/tmp/test-conscio")
        bus.emit("error", "system", {"msg": "test2"}, project_dir="/home/ubuntu/other")
        events = populated_bus.query(project_dir="/tmp/test-conscio") if False else bus.query(project_dir="/tmp/test-conscio")
        for e in events:
            assert e.project_dir == "/tmp/test-conscio"

    def test_query_limit(self, populated_bus):
        """Limit parameter caps result count."""
        events = populated_bus.query(limit=2)
        assert len(events) <= 2

    def test_try_break_negative_limit_is_bounded_not_unbounded(self, populated_bus):
        """I-E2: SQLite LIMIT -1 means UNBOUNDED — a nonsensical negative limit
        must not silently return the whole table."""
        all_events = populated_bus.query(limit=1000, include_duplicates=True)
        assert len(all_events) > 1                       # fixture has several
        got = populated_bus.query(limit=-1, include_duplicates=True)
        assert len(got) < len(all_events)                # not unbounded
        assert len(got) == 0                             # clamped to empty

    def test_query_offset(self, populated_bus):
        """Offset parameter skips results."""
        page1 = populated_bus.query(limit=3, offset=0)
        page2 = populated_bus.query(limit=3, offset=3)
        # Pages should not overlap
        ids1 = {e.id for e in page1}
        ids2 = {e.id for e in page2}
        assert ids1.isdisjoint(ids2)

    def test_query_order_desc(self, populated_bus):
        """Results are ordered newest first."""
        events = populated_bus.query(limit=100)
        timestamps = [e.timestamp for e in events]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_combined_filters(self, populated_bus):
        """Multiple filters work together."""
        events = populated_bus.query(type="error", category="trading", priority_max=PRIORITY_CRITICAL)
        for e in events:
            assert e.type == "error"
            assert e.category == "trading"
            assert e.priority <= PRIORITY_CRITICAL

    def test_recent_errors(self, populated_bus):
        """recent_errors returns error events."""
        errors = populated_bus.recent_errors(limit=5)
        for e in errors:
            assert e.type == "error"
            assert e.priority <= PRIORITY_HIGH

    def test_recent_anomalies(self, populated_bus):
        """recent_anomalies returns anomaly events."""
        anomalies = populated_bus.recent_anomalies()
        for e in anomalies:
            assert e.type == "anomaly"


# ─── Get Tests ──────────────────────────────────────────────────────────

class TestGet:
    def test_get_existing(self, populated_bus):
        """get() returns event for existing ID."""
        event = populated_bus.get(1)
        assert event is not None
        assert isinstance(event, Event)

    def test_get_nonexistent(self, bus):
        """get() returns None for nonexistent ID."""
        assert bus.get(9999) is None


# ─── Event Data Class Tests ─────────────────────────────────────────────

class TestEventDataclass:
    def test_to_dict(self, populated_bus):
        """Event.to_dict() produces valid dict."""
        event = populated_bus.get(1)
        d = event.to_dict()
        assert "id" in d
        assert "type" in d
        assert "data" in d
        assert isinstance(d["data"], dict)

    def test_is_duplicate_field(self, bus):
        """Event has is_duplicate field."""
        eid = bus.emit("error", "system", {"msg": "test"})
        event = bus.get(eid)
        assert hasattr(event, "is_duplicate")
        assert event.is_duplicate is False


# ─── Summary Tests ──────────────────────────────────────────────────────

class TestSummary:
    def test_summary_structure(self, populated_bus):
        """Summary has all expected fields."""
        s = populated_bus.summary(hours=24)
        assert "hours" in s
        assert "total_events" in s
        assert "duplicates_suppressed" in s
        assert "by_type" in s
        assert "by_category" in s
        assert "priority_distribution" in s
        assert "error_highlights" in s

    def test_summary_counts(self, populated_bus):
        """Summary counts match actual events."""
        s = populated_bus.summary(hours=24)
        assert s["total_events"] == 6
        assert "error" in s["by_type"]
        assert "trading" in s["by_category"]

    def test_summary_priority_distribution(self, populated_bus):
        """Priority distribution buckets are correct."""
        s = populated_bus.summary(hours=24)
        dist = s["priority_distribution"]
        assert "critical" in dist
        assert "high" in dist
        assert "normal" in dist
        # Total should match
        assert sum(dist.values()) == s["total_events"]

    def test_summary_error_highlights(self, populated_bus):
        """Error highlights contain actual error data."""
        s = populated_bus.summary(hours=24)
        assert len(s["error_highlights"]) >= 1

    def test_summary_empty(self, bus):
        """Summary with no events returns zeros."""
        s = bus.summary(hours=24)
        assert s["total_events"] == 0


# ─── Compact Tests ──────────────────────────────────────────────────────

class TestCompact:
    def test_compact_removes_trivial(self, bus):
        """compact() removes trivial events older than before_days."""
        # Insert trivial event
        eid = bus.emit("perception", "system", {"cpu": 50}, priority=PRIORITY_TRIVIAL)

        # Backdate it
        old_ts = (naive_utcnow() - timedelta(days=31)).isoformat()
        bus.db.execute("UPDATE events SET timestamp = ? WHERE id = ?", (old_ts, eid))
        bus.db.commit()

        removed = bus.compact(before_days=30)
        assert removed >= 1
        assert bus.get(eid) is None

    def test_compact_preserves_critical(self, bus):
        """compact() preserves critical events."""
        eid = bus.emit("error", "trading", {"msg": "critical"}, priority=PRIORITY_CRITICAL)

        # Backdate it
        old_ts = (naive_utcnow() - timedelta(days=31)).isoformat()
        bus.db.execute("UPDATE events SET timestamp = ? WHERE id = ?", (old_ts, eid))
        bus.db.commit()

        removed = bus.compact(before_days=30)
        assert removed == 0  # No critical events removed
        assert bus.get(eid) is not None

    def test_compact_removes_old_duplicates(self, bus):
        """compact() removes old duplicate entries."""
        eid = bus.emit("error", "system", {"msg": "original"}, priority=PRIORITY_NORMAL)
        bus.mark_duplicate(eid)

        # Backdate
        old_ts = (naive_utcnow() - timedelta(days=31)).isoformat()
        bus.db.execute("UPDATE events SET timestamp = ? WHERE id = ?", (old_ts, eid))
        bus.db.commit()

        removed = bus.compact(before_days=30)
        assert removed >= 1

    def test_compact_preserves_recent(self, bus):
        """compact() doesn't touch recent events."""
        bus.emit("error", "system", {"msg": "recent"}, priority=PRIORITY_TRIVIAL)
        removed = bus.compact(before_days=30)
        assert removed == 0


# ─── Mark Duplicate Tests ───────────────────────────────────────────────

class TestMarkDuplicate:
    def test_mark_duplicate(self, bus):
        """mark_duplicate sets is_duplicate flag."""
        eid = bus.emit("error", "system", {"msg": "test"})
        assert bus.mark_duplicate(eid) is True
        event = bus.get(eid)
        assert event.is_duplicate is True

    def test_mark_nonexistent(self, bus):
        """mark_duplicate returns False for nonexistent event."""
        assert bus.mark_duplicate(9999) is False

    def test_duplicates_excluded_by_default(self, bus):
        """Queries exclude duplicates by default."""
        eid = bus.emit("error", "system", {"msg": "to_mark"})
        bus.mark_duplicate(eid)

        events = bus.query(type="error", include_duplicates=False)
        assert eid not in {e.id for e in events}

    def test_duplicates_included_when_requested(self, bus):
        """Queries include duplicates when flag is set."""
        eid = bus.emit("error", "system", {"msg": "to_mark2"})
        bus.mark_duplicate(eid)

        events = bus.query(type="error", include_duplicates=True)
        assert eid in {e.id for e in events}


# ─── Stats Tests ────────────────────────────────────────────────────────

class TestStats:
    def test_empty_stats(self, bus):
        """Empty bus has zero counts."""
        s = bus.stats()
        assert s["total_events"] == 0
        assert s["duplicates"] == 0

    def test_populated_stats(self, populated_bus):
        """Populated bus has correct counts."""
        s = populated_bus.stats()
        assert s["total_events"] == 6
        assert s["unique_events"] == 6
        assert len(s["by_type"]) > 0


# ─── Context Manager Tests ──────────────────────────────────────────────

class TestContextManager:
    def test_with_statement(self, tmp_path):
        """EventBus works as context manager."""
        with EventBus(db_path=tmp_path / "ctx.db") as b:
            b.emit("error", "system", {"msg": "ctx test"})
            s = b.stats()
            assert s["total_events"] == 1

    def test_close_idempotent(self, bus):
        """close() can be called multiple times."""
        bus.close()
        bus.close()


# ─── Edge Case Tests ────────────────────────────────────────────────────

class TestEdgeCases:
    def test_unicode_data(self, bus):
        """Unicode data is stored and retrieved correctly."""
        eid = bus.emit("error", "trading", {"msg": "Erro de compliance: violação"})
        event = bus.get(eid)
        assert "violação" in event.data["msg"]

    def test_large_payload(self, bus):
        """Large JSON payload is handled."""
        data = {"key": "x" * 10000, "nested": {"deep": list(range(100))}}
        eid = bus.emit("error", "system", data)
        event = bus.get(eid)
        assert len(event.data["key"]) == 10000

    def test_empty_data(self, bus):
        """Empty data dict works."""
        eid = bus.emit("error", "system", {})
        event = bus.get(eid)
        assert event.data == {}

    def test_non_serializable_data(self, bus):
        """Non-JSON-serializable data uses default=str fallback."""
        from datetime import date
        eid = bus.emit("error", "system", {"date": date(2026, 6, 4)})
        event = bus.get(eid)
        assert "2026-06-04" in str(event.data["date"])

    def test_event_bus_shares_db_with_content_store(self, tmp_path):
        """EventBus and ContentStore can coexist in the same DB."""
        from conscio.content_store import ContentStore

        db_path = tmp_path / "shared.db"
        bus = EventBus(db_path=db_path)
        store = ContentStore(db_path=db_path)

        bus.emit("error", "system", {"msg": "test"})
        store.index("test", "Some content", "reflection")

        assert bus.stats()["total_events"] == 1
        assert store.stats()["source_count"] == 1

        bus.close()
        store.close()

    def test_priority_constants(self):
        """Priority constants are in correct order."""
        assert PRIORITY_CRITICAL < PRIORITY_HIGH < PRIORITY_NORMAL < PRIORITY_LOW < PRIORITY_TRIVIAL

    def test_many_events_performance(self, bus):
        """Handles many events without degradation."""
        for i in range(200):
            bus.emit("perception", "system", {"cpu": i % 100, "batch": i})

        s = bus.stats()
        assert s["total_events"] == 200
        events = bus.query(type="perception", limit=50)
        assert len(events) == 50
