"""Performance regression guard: reflect() and dream() at 10k events + 1k entities.

These are coarse guards (generous thresholds), not micro-benchmarks. Their job
is to catch pathological O(n^2) regressions, not to measure absolute speed.

NOTE: marked `slow` — run with `pytest tests/test_perf_reflection.py` directly,
or the whole suite with `-m slow`. Seeding uses a single bulk executemany so
memory stays low.
"""
import time
from datetime import datetime

import pytest

from conscio.engine import ConsciousnessEngine


def _seed(engine, n_events=10_000, n_entities=1_000):
    # Bulk-insert events directly (fast; bypasses per-emit dedup SELECT).
    ts = datetime(2026, 1, 1).isoformat()
    rows = [
        ("perception", "system", "{}", 5, f"hash-{i}", ts)
        for i in range(n_events)
    ]
    engine.event_bus.db.executemany(
        "INSERT INTO events (type, category, data, priority, data_hash, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    engine.event_bus.db.commit()

    # Build entities directly in the world-model dict, then save once.
    now = datetime.now().isoformat()
    for i in range(n_entities):
        engine.world._data["entities"][f"ent-{i}"] = {
            "type": "system", "attributes": {}, "state": "ok",
            "last_updated": now, "relevance": 0.9,
        }
    engine.world._save()


@pytest.fixture
def big_engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    e._session_rag = None  # pin RAG off → reflect()'s recall stays hermetic
    _seed(e)
    yield e
    e.close()


def test_reflect_under_threshold_at_scale(big_engine):
    start = time.perf_counter()
    big_engine.reflect(world_state="system nominal at scale", confidence=0.8)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, f"reflect() took {elapsed:.2f}s at 10k events + 1k entities"


def test_dream_under_threshold_at_scale(big_engine):
    start = time.perf_counter()
    report = big_engine.dream()
    elapsed = time.perf_counter() - start
    assert elapsed < 10.0, f"dream() took {elapsed:.2f}s"
    # With unique hashes, nothing is a duplicate → purge removes 0.
    assert report.events_purged == 0
