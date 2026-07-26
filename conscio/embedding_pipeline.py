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
from .vector_backend import VectorBackend

logger = logging.getLogger(__name__)


class EmbeddingPipeline:
    """Embeds chunk text and stores the resulting vector in a VectorBackend."""

    def __init__(
        self,
        vector_backend: VectorBackend,
        embedding_provider: EmbeddingProvider | None = None,
        dimension: int = 384,
        enabled: bool = True,
    ):
        self.vector_backend = vector_backend
        self.embedding_provider = embedding_provider or EmbeddingProvider()
        self.dimension = dimension
        self.enabled = enabled
        self._seen_hashes: set[str] = set()

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def embed_chunk(self, chunk_id: str, text: str) -> list[float] | None:
        """Embed a single chunk and store it in the vector backend.

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

        try:
            self.vector_backend.add(chunk_id, vec)
        except Exception as e:
            logger.warning(
                f"EmbeddingPipeline.embed_chunk: vector_backend.add failed for {chunk_id}: {e}"
            )
            return None

        self._seen_hashes.add(text_hash)
        return vec

    def embed_batch(self, chunks: list[tuple[str, str]]) -> list[list[float] | None]:
        """Embed multiple (chunk_id, text) pairs in one batch call.

        Uses EmbeddingProvider.embed_batch for speed (single model call for
        all texts) and applies the same hash-based dedup as embed_chunk.
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

        for pos, i in enumerate(to_embed_idx):
            if pos >= len(vecs) or vecs[pos] is None:
                continue
            chunk_id, _text = chunks[i]
            vec = vecs[pos]
            try:
                self.vector_backend.add(chunk_id, vec)
            except Exception as e:
                logger.warning(
                    f"EmbeddingPipeline.embed_batch: vector_backend.add failed for {chunk_id}: {e}"
                )
                continue
            self._seen_hashes.add(to_embed_hashes[pos])
            results[i] = vec

        return results
