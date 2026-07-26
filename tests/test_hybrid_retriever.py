"""Tests for HybridRetriever — RRF fusion of ContentStore FTS5 (lexical) and
VectorBackend (dense) search.

Uses a controlled fake embedder (same injection pattern as
tests/test_embedding_pipeline.py) so vector similarity is fully
deterministic — no dependency on sentence-transformers being installed.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from conscio.content_store import ContentStore
from conscio.embedding import EmbeddingProvider
from conscio.embedding_pipeline import EmbeddingPipeline
from conscio.hybrid_retriever import HybridRetriever
from conscio.vector_backend import VectorBackend

DIM = 4


def _controlled_provider(vectors: dict[str, list[float]]) -> EmbeddingProvider:
    """Real EmbeddingProvider with a mocked internal embedder that returns a
    caller-specified vector for each exact input text (zero-vector for any
    text not in the map). No network, no model download.
    """
    ep = EmbeddingProvider()
    ep.default_dimension = DIM
    mock = MagicMock()
    del mock.embed  # SentenceTransformer shape: .encode(), not .embed()

    def _encode(x):
        if isinstance(x, str):
            return vectors.get(x, [0.0] * DIM)
        return [vectors.get(t, [0.0] * DIM) for t in x]

    mock.encode.side_effect = _encode
    ep._embedder = mock
    return ep


@pytest.fixture
def vector_backend(tmp_path):
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=DIM)
    yield vb
    vb.close()


def _wired_store(tmp_path, vector_backend, vectors):
    provider = _controlled_provider(vectors)
    pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)
    store = ContentStore(
        db_path=tmp_path / "store.db", vector_backend=vector_backend, embeddings=pipeline
    )
    return store, pipeline


# ─── vector_only_search ──────────────────────────────────────────────────

class TestVectorOnlySearch:
    def test_maps_hit_back_to_chunk_rowid_content(self, tmp_path, vector_backend):
        vectors = {
            "alpha content": [1.0, 0.0, 0.0, 0.0],
            "beta content": [0.0, 1.0, 0.0, 0.0],
            "find alpha": [1.0, 0.0, 0.0, 0.0],
        }
        store, pipeline = _wired_store(tmp_path, vector_backend, vectors)
        store.index("doc1", "alpha content", "reference")
        store.index("doc2", "beta content", "reference")

        hr = HybridRetriever(content_store=store, vector_backend=vector_backend,
                              embedding_pipeline=pipeline)
        results = hr.vector_only_search("find alpha", limit=5)

        assert len(results) == 2
        assert results[0].content == "alpha content"  # exact cosine match, ranked first
        store.close()

    def test_category_filter_excludes_other_categories(self, tmp_path, vector_backend):
        vectors = {
            "alpha content": [1.0, 0.0, 0.0, 0.0],
            "beta content": [0.0, 1.0, 0.0, 0.0],
            "find alpha": [1.0, 0.0, 0.0, 0.0],
        }
        store, pipeline = _wired_store(tmp_path, vector_backend, vectors)
        store.index("doc1", "alpha content", "reference")
        store.index("doc2", "beta content", "pentest")

        hr = HybridRetriever(content_store=store, vector_backend=vector_backend,
                              embedding_pipeline=pipeline)
        results = hr.vector_only_search("find alpha", limit=5, category="pentest")

        assert all(r.source_category == "pentest" for r in results)
        assert not any(r.content == "alpha content" for r in results)
        store.close()

    def test_no_vector_backend_returns_empty(self, tmp_path):
        store = ContentStore(db_path=tmp_path / "store.db")
        hr = HybridRetriever(content_store=store, vector_backend=None, embedding_pipeline=None)
        assert hr.vector_only_search("anything") == []
        store.close()

    def test_no_embedder_available_returns_empty(self, tmp_path, vector_backend):
        provider = EmbeddingProvider(force_no_network=True)
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)
        store = ContentStore(
            db_path=tmp_path / "store.db", vector_backend=vector_backend, embeddings=pipeline
        )
        store.index("doc1", "some content", "reference")

        hr = HybridRetriever(content_store=store, vector_backend=vector_backend,
                              embedding_pipeline=pipeline)
        assert hr.vector_only_search("some content") == []
        store.close()

    def test_empty_query_returns_empty(self, tmp_path, vector_backend):
        store, pipeline = _wired_store(tmp_path, vector_backend, {})
        hr = HybridRetriever(content_store=store, vector_backend=vector_backend,
                              embedding_pipeline=pipeline)
        assert hr.vector_only_search("   ") == []
        store.close()

    def test_vector_search_failure_does_not_raise(self, tmp_path, vector_backend):
        vectors = {"doc": [1.0, 0.0, 0.0, 0.0], "q": [1.0, 0.0, 0.0, 0.0]}
        store, pipeline = _wired_store(tmp_path, vector_backend, vectors)
        store.index("doc1", "doc", "reference")

        broken_backend = MagicMock()
        broken_backend.search.side_effect = RuntimeError("db locked")
        hr = HybridRetriever(content_store=store, vector_backend=broken_backend,
                              embedding_pipeline=pipeline)

        assert hr.vector_only_search("q") == []
        store.close()


# ─── search() — standalone combined RRF ─────────────────────────────────

class TestCombinedSearch:
    def test_falls_back_to_lexical_when_no_vector_backend(self, tmp_path):
        store = ContentStore(db_path=tmp_path / "store.db")
        store.index("doc1", "trading bot crashed due to latency spike", "reference")

        hr = HybridRetriever(content_store=store, vector_backend=None, embedding_pipeline=None)
        combined = hr.search("latency spike", k=5)
        lexical_only = store.search("latency spike", limit=5)

        assert [r.rowid for r in combined] == [r.rowid for r in lexical_only]
        store.close()

    def test_alpha_zero_is_pure_lexical(self, tmp_path, vector_backend):
        vectors = {
            "trading bot crashed due to latency spike": [1.0, 0.0, 0.0, 0.0],
            "completely unrelated filler text zzz": [0.0, 1.0, 0.0, 0.0],
            "latency spike": [0.0, 1.0, 0.0, 0.0],  # query vector == unrelated doc's vector
        }
        store, pipeline = _wired_store(tmp_path, vector_backend, vectors)
        store.index("docA", "trading bot crashed due to latency spike", "reference")
        store.index("docB", "completely unrelated filler text zzz", "reference")

        hr = HybridRetriever(content_store=store, vector_backend=vector_backend,
                              embedding_pipeline=pipeline)
        results = hr.search("latency spike", k=5, alpha=0.0)

        # Pure lexical: only docA matches the query terms at all.
        assert len(results) == 1
        assert "unrelated" not in results[0].content
        store.close()

    def test_alpha_one_surfaces_vector_only_hit_on_top(self, tmp_path, vector_backend):
        vectors = {
            "trading bot crashed due to latency spike": [1.0, 0.0, 0.0, 0.0],
            "completely unrelated filler text zzz": [0.0, 1.0, 0.0, 0.0],
            "latency spike": [0.0, 1.0, 0.0, 0.0],  # perfect cosine match with the unrelated doc
        }
        store, pipeline = _wired_store(tmp_path, vector_backend, vectors)
        store.index("docA", "trading bot crashed due to latency spike", "reference")
        store.index("docB", "completely unrelated filler text zzz", "reference")

        hr = HybridRetriever(content_store=store, vector_backend=vector_backend,
                              embedding_pipeline=pipeline)
        results = hr.search("latency spike", k=5, alpha=1.0)

        assert results[0].content == "completely unrelated filler text zzz"
        store.close()

    def test_alpha_changes_ranking_between_extremes(self, tmp_path, vector_backend):
        """Same query, different alpha -> different top result: demonstrates
        alpha actually blends the two rankings rather than being ignored."""
        vectors = {
            "trading bot crashed due to latency spike": [1.0, 0.0, 0.0, 0.0],
            "completely unrelated filler text zzz": [0.0, 1.0, 0.0, 0.0],
            "latency spike": [0.0, 1.0, 0.0, 0.0],
        }
        store, pipeline = _wired_store(tmp_path, vector_backend, vectors)
        store.index("docA", "trading bot crashed due to latency spike", "reference")
        store.index("docB", "completely unrelated filler text zzz", "reference")

        hr = HybridRetriever(content_store=store, vector_backend=vector_backend,
                              embedding_pipeline=pipeline)
        lexical_biased = hr.search("latency spike", k=5, alpha=0.0)
        vector_biased = hr.search("latency spike", k=5, alpha=1.0)

        assert lexical_biased[0].content != vector_biased[0].content
        store.close()

    def test_empty_query_returns_empty(self, tmp_path, vector_backend):
        store, pipeline = _wired_store(tmp_path, vector_backend, {})
        hr = HybridRetriever(content_store=store, vector_backend=vector_backend,
                              embedding_pipeline=pipeline)
        assert hr.search("") == []
        store.close()

    def test_no_vector_hits_falls_back_to_lexical(self, tmp_path, vector_backend):
        """embedding_pipeline/vector_backend configured, but the vector store
        has no matching vectors (e.g. embedder unavailable at index time) ->
        search() must still return the lexical results, not an empty list."""
        provider = EmbeddingProvider(force_no_network=True)
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)
        store = ContentStore(
            db_path=tmp_path / "store.db", vector_backend=vector_backend, embeddings=pipeline
        )
        store.index("doc1", "latency spike happened", "reference")

        hr = HybridRetriever(content_store=store, vector_backend=vector_backend,
                              embedding_pipeline=pipeline)
        results = hr.search("latency spike", k=5, alpha=1.0)

        assert len(results) == 1
        assert results[0].content == "latency spike happened"
        store.close()
