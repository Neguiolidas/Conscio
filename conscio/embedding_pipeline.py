"""EmbeddingPipeline — connects EmbeddingProvider to VectorBackend.

Bridges ContentStore's FTS5 ingestion path to vector search: given a chunk
of text and an id to store it under, embeds the text (via EmbeddingProvider)
and writes the resulting vector into a VectorBackend. Both steps are
best-effort — a missing embedder or a storage failure logs a warning and
returns None rather than raising, so callers (ContentStore.index()) never
have their ingest interrupted by embedding trouble.

Dedup: an in-memory set of sha256(text) hashes, scoped to the pipeline
instance's lifetime. Re-embedding the same chunk text (e.g. ContentStore
re-indexing the same document under a second category, which re-inserts
chunk rows with fresh rowids) is skipped. This is the simplest mechanism
that satisfies the common case: it needs no new schema (VectorBackend has
no "does this id already have a vector" query, and reusing content_hash as
the vector id would conflict with the chunk_rowid keying required for RRF
alignment — see Task 2 brief). Its known limitation is that the cache does
not survive process restarts, so a freshly constructed EmbeddingPipeline in
a new process will re-embed content it embedded in a prior run. That's an
acceptable trade for now: ContentStore.index() already short-circuits
whole-document re-indexing via its own content_hash check before any
chunking happens, so the pipeline mostly needs to dedup within one ingest
session, not across process lifetimes.
"""
from __future__ import annotations

import hashlib
import logging

from .embedding import EmbeddingProvider
from .vector_backend import VectorBackend, SqliteVecBackend, HNSWBackend

# Type alias for any vector backend (all share the same API)
VectorBackendType = VectorBackend | SqliteVecBackend | HNSWBackend

logger = logging.getLogger(__name__)


class EmbeddingPipeline:
    """Embeds chunk text and stores the resulting vector in a VectorBackend."""

    def __init__(
        self,
        vector_backend: VectorBackendType,
        embedding_provider: EmbeddingProvider | None = None,
        dimension: int = 384,
        enabled: bool = True,
    ):
        self.vector_backend = vector_backend
        self.embedding_provider = embedding_provider or EmbeddingProvider()
        self.dimension = dimension
        self.enabled = enabled
        self._seen_hashes: set[str] = set()
        # Dimension reconciliation state (see _reconcile_dimension).
        self._dim_checked = False
        self._store_failed_once = False

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def _reconcile_dimension(self, vec: list[float]) -> bool:
        """Align the vector store with the dimension the embedder ACTUALLY emits.

        The configured dimension comes from env/defaults and is only a guess:
        an Ollama `nomic-embed-text-v1.5` emits 768, a local MiniLM 384, and a
        misconfigured pair used to make EVERY `add()` raise `Dimension
        mismatch` — swallowed as a per-chunk warning, so a 5-minute ingest
        would finish reporting "14000 ingested, 0 failed" with an EMPTY dense
        index, discoverable only by grepping logs.

        Now the first real embedding decides: if the store is still empty it
        adopts the true dimension; if it already holds vectors of another
        dimension the mismatch is unrecoverable, so the pipeline disables
        itself and says so ONCE at ERROR level instead of whispering per chunk.
        """
        if self._dim_checked:
            return self.enabled
        self._dim_checked = True
        dim = len(vec)
        if self.vector_backend.ensure_dimension(dim):
            self.dimension = self.vector_backend.dimension
            return True
        logger.error(
            "EmbeddingPipeline: embedder emits %d-dim vectors but the vector store "
            "already holds %d-dim vectors — dense indexing DISABLED for this run. "
            "Set CONSCIO_EMBED_DIM/CONSCIO_EMBED_MODEL to match, or rebuild the "
            "vector store (delete vectors.db) to re-index at the new dimension.",
            dim, self.vector_backend.dimension,
        )
        self.enabled = False
        return False

    def _store_failure(self, where: str, chunk_id: str, exc: Exception) -> None:
        """Report a storage failure loudly the first time, quietly after that."""
        if not self._store_failed_once:
            self._store_failed_once = True
            logger.error(
                "%s: vector_backend write failed for %s: %s "
                "(further failures logged at debug level)", where, chunk_id, exc,
            )
        else:
            logger.debug("%s: vector_backend write failed for %s: %s", where, chunk_id, exc)

    def embed_chunk(
        self, chunk_id: str, text: str, category: str | None = None
    ) -> list[float] | None:
        """Embed a single chunk and store it in the vector backend.

        `category` is denormalized onto the vector row so category-scoped
        recall can pre-filter candidates in SQL instead of scoring the whole
        index (see VectorBackend.search).

        Returns the embedded vector, or None if: disabled, the chunk text was
        already embedded by this pipeline instance (dedup), no embedder is
        available, or embedding/storage failed.
        """
        if not self.enabled:
            return None

        text_hash = self._hash(text)
        if text_hash in self._seen_hashes:
            return None

        try:
            vec = self.embedding_provider.embed(text)
        except Exception as e:
            logger.warning(f"EmbeddingPipeline.embed_chunk: embed failed for {chunk_id}: {e}")
            return None

        if vec is None:
            return None

        if not self._reconcile_dimension(vec):
            return None

        try:
            self.vector_backend.add(chunk_id, vec, category=category)
        except Exception as e:
            self._store_failure("EmbeddingPipeline.embed_chunk", chunk_id, e)
            return None

        self._seen_hashes.add(text_hash)
        return vec

    def embed_batch(
        self, chunks: list[tuple[str, str]], category: str | None = None
    ) -> list[list[float] | None]:
        """Embed multiple (chunk_id, text) pairs in one batch call.

        Uses EmbeddingProvider.embed_batch for speed (single model call for
        all texts) and applies the same hash-based dedup as embed_chunk, then
        writes every resulting vector in ONE transaction (`add_batch`): the
        old per-vector commit meant one fsync per chunk, i.e. ~200k fsyncs for
        the target corpus, which alone would blow the <5min ingest budget.
        Returns a list aligned with the input, with None at positions that
        were skipped (dedup) or failed.
        """
        results: list[list[float] | None] = [None] * len(chunks)
        if not self.enabled or not chunks:
            return results

        to_embed_idx: list[int] = []
        to_embed_texts: list[str] = []
        to_embed_hashes: list[str] = []
        batch_seen: set[str] = set()  # dedup duplicate texts within this batch too

        for i, (_chunk_id, text) in enumerate(chunks):
            text_hash = self._hash(text)
            if text_hash in self._seen_hashes or text_hash in batch_seen:
                continue
            batch_seen.add(text_hash)
            to_embed_idx.append(i)
            to_embed_texts.append(text)
            to_embed_hashes.append(text_hash)

        if not to_embed_idx:
            return results

        try:
            vecs = self.embedding_provider.embed_batch(to_embed_texts)
        except Exception as e:
            logger.warning(f"EmbeddingPipeline.embed_batch: embed_batch failed: {e}")
            return results

        if vecs is None:
            return results

        pending: list[tuple[str, list[float]]] = []
        pending_pos: list[int] = []
        for pos, i in enumerate(to_embed_idx):
            if pos >= len(vecs) or vecs[pos] is None:
                continue
            chunk_id, _text = chunks[i]
            vec = vecs[pos]
            if not self._reconcile_dimension(vec):
                return results
            pending.append((chunk_id, vec))
            pending_pos.append(pos)

        if not pending:
            return results

        try:
            self.vector_backend.add_batch(pending, category=category)
        except Exception as e:
            # add_batch validates everything before writing, so the batch is
            # all-or-nothing: on failure no result is marked as stored.
            self._store_failure("EmbeddingPipeline.embed_batch", pending[0][0], e)
            return results

        for pos in pending_pos:
            self._seen_hashes.add(to_embed_hashes[pos])
            results[to_embed_idx[pos]] = vecs[pos]

        return results
