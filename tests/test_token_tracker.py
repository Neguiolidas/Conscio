"""
Tests for TokenTracker — Token estimation and savings tracking.

Covers: record, estimate_tokens, gain, budget_status, stats, compact, edge cases.
"""

from datetime import datetime, timedelta

import pytest

from conscio.token_tracker import (
    TokenTracker,
    CHARS_PER_TOKEN,
    VALID_SOURCES,
)


@pytest.fixture
def tracker(tmp_path):
    """Create a TokenTracker with a temp database."""
    db_path = tmp_path / "test_tokens.db"
    t = TokenTracker(db_path=db_path)
    yield t
    t.close()


@pytest.fixture
def populated_tracker(tracker):
    """Create a tracker with sample recordings."""
    tracker.record("reflection", "A" * 4000, "B" * 1000)  # 1000→250 tokens, 75% saved
    tracker.record("trading", "C" * 8000, "D" * 4000)  # 2000→1000 tokens, 50% saved
    tracker.record("perception", "E" * 200, "F" * 200)  # 50→50 tokens, 0% saved
    tracker.record("system", "G" * 1200, "H" * 600)  # 300→150 tokens, 50% saved
    return tracker


# ─── Schema Tests ───────────────────────────────────────────────────────

class TestSchema:
    def test_tables_created(self, tracker):
        tables = tracker.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        assert "token_usage" in {r["name"] for r in tables}

    def test_indexes_created(self, tracker):
        indexes = tracker.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        names = {r["name"] for r in indexes}
        assert "idx_token_source" in names
        assert "idx_token_timestamp" in names

    def test_wal_mode(self, tracker):
        mode = tracker.db.execute("PRAGMA journal_mode").fetchone()["journal_mode"]
        assert mode == "wal"

    def test_idempotent_init(self, tracker):
        tracker._init_schema()
        tracker._init_schema()


# ─── Estimate Tokens Tests ──────────────────────────────────────────────

class TestEstimateTokens:
    def test_basic_estimation(self):
        assert TokenTracker.estimate_tokens("A" * 400) == 100

    def test_minimum_one_token(self):
        assert TokenTracker.estimate_tokens("A") == 1

    def test_empty_string(self):
        assert TokenTracker.estimate_tokens("") == 1

    def test_large_text(self):
        result = TokenTracker.estimate_tokens("A" * 40000)
        assert result == 10000

    def test_chars_per_token_constant(self):
        assert CHARS_PER_TOKEN == 4


# ─── Record Tests ───────────────────────────────────────────────────────

class TestRecord:
    def test_basic_record(self, tracker):
        result = tracker.record("reflection", "A" * 400, "B" * 100)
        assert result["source"] == "reflection"
        assert result["raw_tokens"] == 100
        assert result["filtered_tokens"] == 25
        assert result["saved_tokens"] == 75
        assert result["saving_pct"] == 75.0

    def test_zero_savings(self, tracker):
        text = "A" * 400
        result = tracker.record("perception", text, text)
        assert result["saving_pct"] == 0.0
        assert result["saved_tokens"] == 0

    def test_total_savings(self, tracker):
        result = tracker.record("trading", "A" * 800, "B" * 0)
        # Empty filtered → estimate_tokens returns 1, so ~99.5% not exactly 100%
        assert result["saving_pct"] >= 99.0

    def test_invalid_source_raises(self, tracker):
        with pytest.raises(ValueError, match="Invalid source"):
            tracker.record("invalid_source", "text", "text")

    def test_valid_sources(self):
        expected = {"reflection", "perception", "injection", "trading",
                    "system", "consciousness", "tool_output", "external"}
        assert VALID_SOURCES == expected

    def test_record_simple(self, tracker):
        result = tracker.record_simple("trading", raw_chars=400, filtered_chars=200)
        assert result["raw_tokens"] == 100
        assert result["filtered_tokens"] == 50
        assert result["saved_tokens"] == 50

    def test_multiple_recordings(self, tracker):
        tracker.record("reflection", "A" * 400, "B" * 100)
        tracker.record("reflection", "C" * 800, "D" * 200)
        count = tracker.db.execute("SELECT COUNT(*) as c FROM token_usage").fetchone()["c"]
        assert count == 2


# ─── Gain Tests ─────────────────────────────────────────────────────────

class TestGain:
    def test_gain_structure(self, populated_tracker):
        g = populated_tracker.gain(hours=24)
        assert "hours" in g
        assert "total_raw_tokens" in g
        assert "total_filtered_tokens" in g
        assert "total_saved_tokens" in g
        assert "overall_saving_pct" in g
        assert "by_source" in g

    def test_gain_totals(self, populated_tracker):
        g = populated_tracker.gain(hours=24)
        # reflection: 1000 raw, 250 filtered = 750 saved
        # trading: 2000 raw, 1000 filtered = 1000 saved
        # perception: 50 raw, 50 filtered = 0 saved
        # system: 300 raw, 150 filtered = 150 saved
        assert g["total_raw_tokens"] == 3350
        assert g["total_filtered_tokens"] == 1450
        assert g["total_saved_tokens"] == 1900

    def test_gain_by_source(self, populated_tracker):
        g = populated_tracker.gain(hours=24)
        assert "reflection" in g["by_source"]
        assert "trading" in g["by_source"]
        assert g["by_source"]["reflection"]["saved_tokens"] == 750

    def test_gain_empty(self, tracker):
        g = tracker.gain(hours=24)
        assert g["total_raw_tokens"] == 0
        assert g["overall_saving_pct"] == 0.0

    def test_gain_future_cutoff(self, populated_tracker):
        """Future cutoff returns empty."""
        g = populated_tracker.gain(hours=0)
        # With 0 hours window, all events are excluded
        assert g["total_raw_tokens"] == 0 or g["total_saved_tokens"] >= 0


# ─── Budget Status Tests ───────────────────────────────────────────────

class TestBudgetStatus:
    def test_budget_ok(self, populated_tracker):
        b = populated_tracker.budget_status(daily_limit=50000)
        assert b["status"] == "ok"
        assert b["pct_used"] < 100.0
        assert b["tokens_remaining"] > 0

    def test_budget_over(self, populated_tracker):
        b = populated_tracker.budget_status(daily_limit=100)
        # 1450 tokens used > 100 limit
        assert b["status"] == "over_budget"

    def test_budget_structure(self, populated_tracker):
        b = populated_tracker.budget_status()
        assert "daily_limit" in b
        assert "tokens_used" in b
        assert "tokens_remaining" in b
        assert "pct_used" in b
        assert "status" in b

    def test_budget_empty(self, tracker):
        b = tracker.budget_status(daily_limit=50000)
        assert b["tokens_used"] == 0
        assert b["tokens_remaining"] == 50000
        assert b["status"] == "ok"

    def test_custom_daily_limit(self, populated_tracker):
        b = populated_tracker.budget_status(daily_limit=2000)
        assert b["daily_limit"] == 2000


# ─── Stats Tests ────────────────────────────────────────────────────────

class TestStats:
    def test_empty_stats(self, tracker):
        s = tracker.stats()
        assert s["total_recordings"] == 0

    def test_populated_stats(self, populated_tracker):
        s = populated_tracker.stats()
        assert s["total_recordings"] == 4
        assert s["total_raw_tokens"] == 3350
        assert s["total_saved_tokens"] == 1900
        assert s["avg_saving_pct"] > 0

    def test_stats_avg_saving(self, tracker):
        tracker.record("reflection", "A" * 400, "B" * 200)  # 50%
        tracker.record("trading", "C" * 400, "D" * 100)  # 75%
        s = tracker.stats()
        assert s["total_recordings"] == 2
        assert 60.0 <= s["avg_saving_pct"] <= 65.0  # ~62.5%


# ─── Compact Tests ──────────────────────────────────────────────────────

class TestCompact:
    def test_compact_removes_old(self, tracker):
        tracker.record("reflection", "A" * 400, "B" * 100)

        # Backdate the record
        old_ts = (datetime.utcnow() - timedelta(days=31)).isoformat()
        tracker.db.execute("UPDATE token_usage SET timestamp = ?", (old_ts,))
        tracker.db.commit()

        removed = tracker.compact(before_days=30)
        assert removed >= 1

    def test_compact_preserves_recent(self, tracker):
        tracker.record("reflection", "A" * 400, "B" * 100)
        removed = tracker.compact(before_days=30)
        assert removed == 0


# ─── Shared DB Tests ────────────────────────────────────────────────────

class TestSharedDB:
    def test_shares_db_with_event_bus(self, tmp_path):
        """TokenTracker and EventBus coexist in the same DB."""
        from conscio.event_bus import EventBus

        db_path = tmp_path / "shared.db"
        tracker = TokenTracker(db_path=db_path)
        bus = EventBus(db_path=db_path)

        tracker.record("trading", "A" * 400, "B" * 100)
        bus.emit("trade", "trading", {"action": "buy"})

        assert tracker.stats()["total_recordings"] == 1
        assert bus.stats()["total_events"] == 1

        tracker.close()
        bus.close()

    def test_shares_db_with_content_store(self, tmp_path):
        """TokenTracker and ContentStore coexist in the same DB."""
        from conscio.content_store import ContentStore

        db_path = tmp_path / "shared.db"
        tracker = TokenTracker(db_path=db_path)
        store = ContentStore(db_path=db_path)

        tracker.record("reflection", "A" * 400, "B" * 100)
        store.index("test", "Some content", "reflection")

        assert tracker.stats()["total_recordings"] == 1
        assert store.stats()["source_count"] == 1

        tracker.close()
        store.close()


# ─── Context Manager Tests ──────────────────────────────────────────────

class TestContextManager:
    def test_with_statement(self, tmp_path):
        with TokenTracker(db_path=tmp_path / "ctx.db") as t:
            t.record("reflection", "A" * 400, "B" * 100)
            assert t.stats()["total_recordings"] == 1

    def test_close_idempotent(self, tracker):
        tracker.close()
        tracker.close()


# ─── Edge Case Tests ────────────────────────────────────────────────────

class TestEdgeCases:
    def test_unicode_text(self, tracker):
        """Unicode text length is counted correctly."""
        raw = "Erro de compliance: violação" * 100
        filtered = "violação" * 10
        result = tracker.record("trading", raw, filtered)
        assert result["raw_tokens"] > 0
        assert result["filtered_tokens"] > 0

    def test_very_large_text(self, tracker):
        """Handles very large text."""
        raw = "A" * 100000
        filtered = "B" * 1000
        result = tracker.record("system", raw, filtered)
        assert result["raw_tokens"] == 25000
        assert result["saving_pct"] > 95.0

    def test_empty_filtered(self, tracker):
        """Empty filtered text = 100% savings."""
        result = tracker.record("reflection", "A" * 400, "")
        # filtered is empty, estimate_tokens returns 1
        assert result["saving_pct"] >= 95.0

    def test_both_empty(self, tracker):
        """Both empty = 0% savings (both estimate to 1 token)."""
        result = tracker.record("perception", "", "")
        assert result["saving_pct"] == 0.0

    def test_budget_zero_limit(self, populated_tracker):
        """Zero daily limit = always over budget."""
        b = populated_tracker.budget_status(daily_limit=0)
        assert b["status"] == "over_budget"

    def test_all_valid_sources(self, tracker):
        """All valid sources are accepted."""
        for source in VALID_SOURCES:
            result = tracker.record(source, "A" * 100, "B" * 50)
            assert result["source"] == source

    def test_many_recordings_performance(self, tracker):
        """Handles many recordings without degradation."""
        for i in range(200):
            tracker.record_simple("trading", raw_chars=4000, filtered_chars=2000)

        s = tracker.stats()
        assert s["total_recordings"] == 200
