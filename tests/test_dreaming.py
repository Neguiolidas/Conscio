"""Tests for DreamCycle — consolidation orchestrator."""
from datetime import datetime, timedelta

import pytest

from conscio.dreaming import DreamCycle, DreamReport
from conscio.engine import ConsciousnessEngine
from conscio.timeutil import naive_utcnow


@pytest.fixture
def engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    yield e
    e.close()


def _seed_duplicate_events(engine):
    """Insert duplicate event rows directly (bypass 60s dedup window)."""
    ts = datetime(2026, 1, 1).isoformat()
    for _ in range(4):
        engine.event_bus.db.execute(
            "INSERT INTO events (type, category, data, priority, data_hash, timestamp) "
            "VALUES ('perception','system','{}',5,'dh',?)",
            (ts,),
        )
    engine.event_bus.db.commit()


def test_dream_returns_report(engine):
    report = engine.dream()
    assert isinstance(report, DreamReport)
    assert report.dry_run is False
    assert report.duration_ms >= 0


def test_dream_release_purges_duplicate_events(engine):
    _seed_duplicate_events(engine)
    report = engine.dream()
    assert report.events_purged == 3  # 4 dup rows → 1 kept
    remaining = engine.event_bus.db.execute(
        "SELECT COUNT(*) c FROM events WHERE data_hash='dh'"
    ).fetchone()["c"]
    assert remaining == 1


def test_dream_prune_removes_stale_entity(engine):
    engine.world.add_entity("ghost", "system")
    # Entropy prunes on age compounded with isolation/irrelevance — a genuinely
    # old (30d), isolated, faded entity crosses the 0.85 threshold; a fresh one
    # would be kept by design (recency rescues).
    old = (datetime.now() - timedelta(days=30)).isoformat()
    engine.world._data["entities"]["ghost"]["relevance"] = 0.0
    engine.world._data["entities"]["ghost"]["last_updated"] = old
    engine.world._save()
    report = engine.dream()
    assert "ghost" in report.entities_pruned
    assert engine.world.get_entity("ghost") is None


def test_dream_dry_run_mutates_nothing(engine):
    _seed_duplicate_events(engine)
    engine.world.add_entity("ghost", "system")
    engine.world._data["entities"]["ghost"]["relevance"] = 0.0
    engine.world._save()
    before_events = engine.event_bus.db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    report = engine.dream(dry_run=True)
    after_events = engine.event_bus.db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    assert report.dry_run is True
    assert report.events_purged == 3  # reported, not applied
    assert after_events == before_events
    assert engine.world.get_entity("ghost") is not None


def test_dream_crystallize_consolidates_old_reflections(engine):
    # Seed 5 old reflections (older than crystallize_after_days)
    old_ts = (naive_utcnow() - timedelta(days=30)).isoformat()
    for i in range(5):
        sid = engine.content_store.index(
            label=f"reflection_old_{i}", content=f"old reflection {i}", category="reflection"
        )
        engine.content_store.db.execute(
            "UPDATE sources SET indexed_at=? WHERE id=?", (old_ts, sid)
        )
    engine.content_store.db.commit()
    cycle = DreamCycle(crystallize_after_days=14, crystallize_min_count=3)
    report = cycle.run(engine)
    assert report.reflections_consolidated == 5
    # A consolidated summary now exists in the 'consciousness' category
    summary_sources = engine.content_store.db.execute(
        "SELECT COUNT(*) c FROM sources WHERE source_category='consciousness'"
    ).fetchone()["c"]
    assert summary_sources >= 1
    # The crystal summary survived deletion and holds the consolidated content
    crystal = engine.content_store.db.execute(
        "SELECT content FROM chunks WHERE source_category='consciousness' LIMIT 1"
    ).fetchone()
    assert crystal is not None
    assert "Crystallized reflections" in crystal["content"]
    # Old reflection sources were removed
    remaining_refl = engine.content_store.db.execute(
        "SELECT COUNT(*) c FROM sources WHERE source_category='reflection'"
    ).fetchone()["c"]
    assert remaining_refl == 0


def test_dream_crystallize_skips_when_below_threshold(engine):
    old_ts = (naive_utcnow() - timedelta(days=30)).isoformat()
    sid = engine.content_store.index(label="reflection_one", content="only one", category="reflection")
    engine.content_store.db.execute("UPDATE sources SET indexed_at=? WHERE id=?", (old_ts, sid))
    engine.content_store.db.commit()
    cycle = DreamCycle(crystallize_after_days=14, crystallize_min_count=3)
    report = cycle.run(engine)
    assert report.reflections_consolidated == 0
    assert engine.content_store.get_source(sid) is not None  # untouched


# ── Release also covers the token ledger ───────────────────────────────────


def _seed_old_token_usage(engine, n, days_old=90):
    """Rows older than the compaction window, written straight to the table."""
    ts = (naive_utcnow() - timedelta(days=days_old)).isoformat()
    for _ in range(n):
        engine.token_tracker.db.execute(
            "INSERT INTO token_usage (source, raw_chars, filtered_chars,"
            " raw_tokens, filtered_tokens, saving_pct, timestamp)"
            " VALUES ('reflection', 100, 100, 25, 25, 0.0, ?)", (ts,))
    engine.token_tracker.db.commit()


def _token_rows(engine):
    return engine.token_tracker.db.execute(
        "SELECT COUNT(*) c FROM token_usage").fetchone()["c"]


def test_dream_release_compacts_the_token_ledger(engine):
    """Every reflect() writes a token_usage row and nothing ever deleted one.

    Field report (v3.9.4): 733,949 rows in a live daemon's token_usage while
    every other store in conscio.db was being pruned. TokenTracker.compact()
    has existed since v0.2 with no caller — the retention policy was written
    and never wired.
    """
    _seed_old_token_usage(engine, 5)
    report = engine.dream()
    assert report.token_records_compacted == 5
    assert _token_rows(engine) == 0


def test_dream_keeps_token_records_inside_the_window(engine):
    _seed_old_token_usage(engine, 3, days_old=1)
    report = engine.dream()
    assert report.token_records_compacted == 0
    assert _token_rows(engine) == 3


def test_dream_dry_run_leaves_the_token_ledger_alone(engine):
    _seed_old_token_usage(engine, 4)
    engine.dream(dry_run=True)
    assert _token_rows(engine) == 4
