"""Tests for engine.recall() cross-session memory + reflect integration."""
import pytest

from conscio.content_layer import _RAG_DISABLED
from conscio.engine import ConsciousnessEngine


@pytest.fixture
def engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    e.content_layer._session_rag = _RAG_DISABLED  # pin RAG off → hermetic (no Ollama probe in tests)
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
    # Fixture pins engine.content_layer._session_rag = None (RAG unavailable).
    # recall() must still return ContentStore FTS5 hits.
    assert engine.content_layer.session_rag is None
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


def test_recall_prioritizes_processing_layer_on_near_tie(tmp_path):
    from conscio.content_layer import _RAG_DISABLED
    from conscio.engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name="claude-opus-4-8", storage_path=tmp_path)
    eng.content_layer._session_rag = _RAG_DISABLED  # hermetic: no Ollama probe

    # Both docs match the query. The ROUTINE doc is SHORTER (just the query terms),
    # so BM25 ranks it higher pre-reorder — without the layer reorder it sorts first.
    # The PROCESSING doc is longer/lower-BM25 but co-retrieved. The reorder flips it.
    # category="system" → ROUTINE layer; category="consciousness" → PROCESSING (default).
    eng.content_store.index(
        label="sysmetric", content="alpha beta gamma",
        category="system", content_type="prose",
    )
    eng.content_store.index(
        label="reflect",
        content="alpha beta gamma reflected insight with extra padding words here today",
        category="consciousness", content_type="prose",
    )

    snippets = eng.recall("alpha beta gamma", k=2)
    assert len(snippets) == 2
    # PROCESSING content surfaces first once the layer reorder is applied.
    assert "reflected insight" in snippets[0]


class TestRecallSignatureUnchangedWithVectorBackend:
    """Regression guard (v3.6): wiring VectorBackend/EmbeddingPipeline/
    HybridRetriever into ConsciousnessEngine (opt-in via CONSCIO_VECTORS,
    same pattern as CONSCIO_SEMANTIC_DEDUP — off by default so existing
    installs don't silently start embedding on every index() call) must NOT
    change engine.recall()'s public contract. Production callers —
    mcp/server.py ("snippets" key), agency/relay_cognize.py, and engine.py's
    own reflect/goal/dream paths — depend on the exact signature (query,
    k=3, categories=None) and on a list[str] return, not list[SearchResult].
    """

    @pytest.fixture
    def engine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONSCIO_VECTORS", "1")
        e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
        e.content_layer._session_rag = _RAG_DISABLED  # hermetic (no Ollama probe)
        yield e
        e.close()

    def test_signature_unchanged(self, engine):
        import inspect
        sig = inspect.signature(engine.recall)
        params = list(sig.parameters.values())
        assert [p.name for p in params] == ["query", "k", "categories"]
        assert params[1].default == 3
        assert params[2].default is None
        assert sig.return_annotation in ("list[str]", list, "list")

    def test_hybrid_retriever_is_wired_in_by_default(self, engine):
        assert engine._hybrid_retriever is not None
        assert engine.content_layer._hybrid_retriever is engine._hybrid_retriever

    def test_returns_list_of_str_with_real_vector_hit(self, engine):
        """Force an actual vector-search hit (deterministic injected
        embedder) and confirm recall() still returns list[str] snippets —
        the hybrid third source must fuse transparently, never changing the
        public return shape to SearchResult objects."""
        from unittest.mock import MagicMock

        vec = [1.0, 0.0, 0.0, 0.0]
        # Match the injected vector's dimension (engine defaults to 384).
        engine.vector_backend.dimension = 4
        mock = MagicMock()
        del mock.embed  # SentenceTransformer shape: .encode(), not .embed()
        mock.encode.side_effect = lambda x: vec if isinstance(x, str) else [vec for _ in x]
        engine.embedding_pipeline.embedding_provider._embedder = mock
        engine.embedding_pipeline.embedding_provider.default_dimension = 4

        engine.content_store.index(
            label="vecdoc", content="vector-searchable unique payload",
            category="reflection",
        )
        hits = engine.recall("vector-searchable unique payload", k=3)

        assert isinstance(hits, list)
        assert all(isinstance(h, str) for h in hits)
        assert any("vector-searchable" in h for h in hits)

    def test_categories_param_still_accepted_with_hybrid_wired(self, engine):
        out = engine.recall("anything at all", k=1, categories=["reflection"])
        assert isinstance(out, list)


class TestVectorsAutoDetect:
    """v3.6.1: VectorBackend auto-detects sentence_transformers.
    CONSCIO_VECTORS=0 forces disable. When env absent and dep available,
    vectors auto-enable. This test class mocks the import to control behavior."""

    def test_vector_backend_none_with_env_zero(self, tmp_path, monkeypatch):
        """CONSCIO_VECTORS=0 disables even if dep is available."""
        monkeypatch.setenv("CONSCIO_VECTORS", "0")
        e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
        try:
            e.content_layer._session_rag = _RAG_DISABLED
            assert e.vector_backend is None
            assert e.embedding_pipeline is None
            assert e.content_store.vector_backend is None
            assert e.content_store.embeddings is None
            assert "vector_count" not in e.content_store.stats()
        finally:
            e.close()

    def test_vector_backend_present_with_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONSCIO_VECTORS", "1")
        e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
        try:
            e.content_layer._session_rag = _RAG_DISABLED
            assert e.vector_backend is not None
            assert e.embedding_pipeline is not None
            assert e.content_store.vector_backend is e.vector_backend
        finally:
            e.close()
