"""
Tests for EmbeddingPipeline — connects EmbeddingProvider to VectorBackend.

Covers: single embed, disabled mode, batch embed, dedup, and integration
with ContentStore.index() (including the chunk_rowid-keying regression
guard for the plan's original f"chunk:{source_id}" bug).

None of these tests depend on sentence-transformers actually being
installed: EmbeddingProvider's internal `_embedder` is injected directly
(same pattern as tests/test_embedding.py), so they exercise the same code
paths whether or not a real model is available in this environment.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from conscio.content_store import ContentStore
from conscio.embedding import EmbeddingProvider
from conscio.embedding_pipeline import EmbeddingPipeline
from conscio.vector_backend import VectorBackend

DIM = 8


def _fake_provider(call_log: list | None = None) -> EmbeddingProvider:
    """Real EmbeddingProvider with a mocked internal embedder — no network,
    no model download, deterministic dimension-DIM output per call.
    """
    ep = EmbeddingProvider()
    ep.default_dimension = DIM
    mock = MagicMock()
    del mock.embed  # SentenceTransformer shape: has .encode(), not .embed()

    calls = call_log if call_log is not None else []

    def _encode(x):
        calls.append(x)
        if isinstance(x, str):
            return [float(len(calls))] * DIM
        return [[float(len(calls))] * DIM for _ in x]

    mock.encode.side_effect = _encode
    ep._embedder = mock
    return ep


@pytest.fixture
def vector_backend(tmp_path):
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=DIM)
    yield vb
    vb.close()


# ─── EmbeddingPipeline.embed_chunk ──────────────────────────────────────

class TestEmbedChunk:
    def test_embeds_and_stores(self, vector_backend):
        provider = _fake_provider()
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)

        vec = pipeline.embed_chunk("chunk:1", "hello world")

        assert vec is not None
        assert len(vec) == DIM
        assert vector_backend.stats()["vectors"] == 1

    def test_disabled_mode_returns_none(self, vector_backend):
        provider = _fake_provider()
        pipeline = EmbeddingPipeline(
            vector_backend, embedding_provider=provider, dimension=DIM, enabled=False
        )

        vec = pipeline.embed_chunk("chunk:1", "hello world")

        assert vec is None
        assert vector_backend.stats()["vectors"] == 0

    def test_no_embedder_available_returns_none(self, vector_backend):
        """force_no_network provider -> embed() returns None -> pipeline returns None.

        This exercises the real fallback path without depending on whether
        sentence-transformers happens to be installed in this environment.
        """
        provider = EmbeddingProvider(force_no_network=True)
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)

        vec = pipeline.embed_chunk("chunk:1", "hello world")

        assert vec is None
        assert vector_backend.stats()["vectors"] == 0

    def test_dedup_same_text_not_reembedded(self, vector_backend):
        calls: list = []
        provider = _fake_provider(calls)
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)

        first = pipeline.embed_chunk("chunk:1", "same text")
        second = pipeline.embed_chunk("chunk:2", "same text")  # same text, different id

        assert first is not None
        assert second is None  # deduped by content hash, not re-embedded
        assert len(calls) == 1
        assert vector_backend.stats()["vectors"] == 1  # only chunk:1 stored

    def test_different_text_not_deduped(self, vector_backend):
        calls: list = []
        provider = _fake_provider(calls)
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)

        pipeline.embed_chunk("chunk:1", "text one")
        pipeline.embed_chunk("chunk:2", "text two")

        assert len(calls) == 2
        assert vector_backend.stats()["vectors"] == 2

    def test_embed_failure_does_not_raise(self, vector_backend):
        provider = EmbeddingProvider()
        broken = MagicMock()
        del broken.embed
        broken.encode.side_effect = RuntimeError("boom")
        provider._embedder = broken
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)

        vec = pipeline.embed_chunk("chunk:1", "hello")

        assert vec is None
        assert vector_backend.stats()["vectors"] == 0


# ─── EmbeddingPipeline.embed_batch ──────────────────────────────────────

class TestEmbedBatch:
    def test_batch_embeds_all(self, vector_backend):
        calls: list = []
        provider = _fake_provider(calls)
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)

        results = pipeline.embed_batch(
            [("chunk:1", "text one"), ("chunk:2", "text two"), ("chunk:3", "text three")]
        )

        assert len(results) == 3
        assert all(v is not None and len(v) == DIM for v in results)
        assert vector_backend.stats()["vectors"] == 3
        assert len(calls) == 1  # single batch call to the underlying provider

    def test_batch_dedup_skips_duplicate_text(self, vector_backend):
        provider = _fake_provider()
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)

        results = pipeline.embed_batch(
            [("chunk:1", "repeat"), ("chunk:2", "repeat"), ("chunk:3", "unique")]
        )

        assert results[0] is not None
        assert results[1] is None  # duplicate text within the same batch, deduped
        assert results[2] is not None
        assert vector_backend.stats()["vectors"] == 2

    def test_batch_disabled_returns_all_none(self, vector_backend):
        provider = _fake_provider()
        pipeline = EmbeddingPipeline(
            vector_backend, embedding_provider=provider, dimension=DIM, enabled=False
        )

        results = pipeline.embed_batch([("chunk:1", "a"), ("chunk:2", "b")])

        assert results == [None, None]
        assert vector_backend.stats()["vectors"] == 0

    def test_batch_empty_input(self, vector_backend):
        provider = _fake_provider()
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)

        assert pipeline.embed_batch([]) == []

    def test_batch_no_embedder_available(self, vector_backend):
        provider = EmbeddingProvider(force_no_network=True)
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)

        results = pipeline.embed_batch([("chunk:1", "a"), ("chunk:2", "b")])

        assert results == [None, None]
        assert vector_backend.stats()["vectors"] == 0


# ─── ContentStore integration ───────────────────────────────────────────

class TestContentStoreIntegration:
    def test_index_without_pipeline_unaffected(self, tmp_path):
        """Zero vector_backend/embeddings args -> no vector side effects,
        identical behavior to every pre-Task-2 caller."""
        store = ContentStore(db_path=tmp_path / "store.db")
        source_id = store.index("doc", "Some content here.", "reflection")

        assert source_id == 1
        assert store.vector_backend is None
        assert store.embeddings is None
        store.close()

    def test_index_with_pipeline_embeds_chunk(self, tmp_path, vector_backend):
        provider = _fake_provider()
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)
        store = ContentStore(
            db_path=tmp_path / "store.db", vector_backend=vector_backend, embeddings=pipeline
        )

        store.index("doc", "Some content to embed.", "reflection")

        assert vector_backend.stats()["vectors"] == 1
        store.close()

    def test_vector_search_after_index_maps_to_chunk_rowid(self, tmp_path, vector_backend):
        """Vector search hits key on the same chunks.rowid FTS5 search uses,
        needed for Ato 3's hybrid RRF merge."""
        provider = _fake_provider()
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)
        store = ContentStore(
            db_path=tmp_path / "store.db", vector_backend=vector_backend, embeddings=pipeline
        )

        store.index("doc", "Vector searchable content.", "reflection")

        results = vector_backend.search([1.0] * DIM, limit=5)
        assert len(results) == 1
        vec_id = results[0]["id"]
        assert vec_id.startswith("chunk:")
        chunk_rowid = int(vec_id.split(":", 1)[1])

        row = store.db.execute(
            "SELECT content FROM chunks WHERE rowid = ?", (chunk_rowid,)
        ).fetchone()
        assert row is not None
        assert row["content"] == "Vector searchable content."
        store.close()

    def test_multi_chunk_document_each_chunk_gets_own_vector(self, tmp_path, vector_backend):
        """Regression guard: the plan's illustrative f"chunk:{source_id}"
        snippet would key every chunk of one document under the same id,
        and VectorBackend.add() is INSERT OR REPLACE, so all but the last
        chunk's vector would be silently overwritten. Each chunk must get
        its own vector.
        """
        provider = _fake_provider()
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)
        store = ContentStore(
            db_path=tmp_path / "store.db", vector_backend=vector_backend, embeddings=pipeline
        )

        long_content = "\n\n".join(f"Paragraph {i} " + ("x" * 50) for i in range(10))
        store.index("multi", long_content, "reflection", chunk_size=200)

        chunk_count = store.db.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
        assert chunk_count > 1  # confirms the document was actually split
        assert vector_backend.stats()["vectors"] == chunk_count
        store.close()

    def test_index_embedding_failure_does_not_break_fts5_ingest(self, tmp_path, vector_backend):
        """If the embedder raises, FTS5 ingest must still succeed — embedding
        is a best-effort side channel, never a gate on the primary path."""
        provider = EmbeddingProvider()
        broken = MagicMock()
        del broken.embed
        broken.encode.side_effect = RuntimeError("boom")
        provider._embedder = broken
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)
        store = ContentStore(
            db_path=tmp_path / "store.db", vector_backend=vector_backend, embeddings=pipeline
        )

        source_id = store.index("doc", "This should still be indexed.", "reflection")

        assert source_id == 1
        results = store.search("indexed")
        assert len(results) == 1
        assert vector_backend.stats()["vectors"] == 0  # embedding failed, nothing stored
        store.close()

    def test_reindex_same_content_new_category_dedups_embedding(self, tmp_path, vector_backend):
        """ContentStore.index() re-inserts chunk rows (with fresh rowids) when
        the same content is indexed under a new category. The pipeline's
        text-hash dedup should skip re-embedding identical chunk text even
        though it gets a new chunk_rowid-keyed vector id."""
        calls: list = []
        provider = _fake_provider(calls)
        pipeline = EmbeddingPipeline(vector_backend, embedding_provider=provider, dimension=DIM)
        store = ContentStore(
            db_path=tmp_path / "store.db", vector_backend=vector_backend, embeddings=pipeline
        )

        store.index("doc", "Shared content.", "reflection")
        store.index("doc", "Shared content.", "system")  # same content, new category

        assert len(calls) == 1  # embedded once, not twice
        assert vector_backend.stats()["vectors"] == 1
        store.close()
