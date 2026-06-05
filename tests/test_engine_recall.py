"""Tests for engine.recall() cross-session memory + reflect integration."""
import pytest

from conscio.engine import ConsciousnessEngine


@pytest.fixture
def engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    e._session_rag = None  # pin RAG off → hermetic (no Ollama probe in tests)
    yield e
    e.close()


def test_recall_returns_fts5_hits(engine):
    engine.content_store.index(
        label="past_note",
        content="the trading bot crashed due to an order book latency spike",
        category="reflection",
    )
    hits = engine.recall("order book latency", k=3)
    assert any("latency" in h for h in hits)


def test_recall_empty_query_returns_empty(engine):
    assert engine.recall("   ", k=3) == []


def test_recall_bounded_by_k(engine):
    for i in range(10):
        engine.content_store.index(
            label=f"note_{i}", content=f"latency event number {i} occurred",
            category="reflection",
        )
    hits = engine.recall("latency event", k=3)
    assert len(hits) <= 3


def test_recall_snippets_are_length_bounded(engine):
    engine.content_store.index(
        label="long", content="latency " + ("x" * 5000), category="reflection"
    )
    hits = engine.recall("latency", k=1)
    assert hits and all(len(h) <= 320 for h in hits)


def test_recall_graceful_when_rag_unavailable(engine):
    # Fixture pins engine._session_rag = None (RAG unavailable).
    # recall() must still return ContentStore FTS5 hits.
    assert engine.session_rag is None
    engine.content_store.index(label="n", content="latency spike happened", category="reflection")
    hits = engine.recall("latency spike", k=2)
    assert any("latency" in h for h in hits)


def test_reflect_injects_past_context(engine, monkeypatch):
    engine.content_store.index(
        label="memory", content="previously the latency spike came from the cache",
        category="reflection",
    )
    captured = {}
    original = engine.monologue.reflect

    def spy(*args, **kwargs):
        captured["recent_events"] = kwargs.get("recent_events")
        return original(*args, **kwargs)

    monkeypatch.setattr(engine.monologue, "reflect", spy)
    engine.reflect(world_state="latency spike investigation", confidence=0.7)
    joined = " ".join(captured.get("recent_events") or [])
    assert "recall" in joined.lower() or "latency" in joined.lower()
