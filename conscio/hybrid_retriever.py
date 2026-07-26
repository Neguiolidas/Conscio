"""HybridRetriever — RRF fusion of ContentStore FTS5 (lexical) and
VectorBackend (dense) search.

ContentStore's FTS5 search already merges two lexical indexes (porter +
trigram) via Reciprocal Rank Fusion. HybridRetriever adds a *dense* leg on
top: it embeds the query with the same EmbeddingPipeline used at ingest
time, searches VectorBackend for nearest neighbors, and maps the resulting
`chunk:<rowid>` ids back onto the same `chunks.rowid` space that FTS5
results live in — so both rankings can be fused directly, with no
id-translation step.

Two entry points, two different callers:
  - `vector_only_search()`: the dense leg alone. This is what
    ContentLayerManager.recall() calls — it already owns the lexical RRF
    via ContentStore.search(), so it only needs HybridRetriever's vector
    results as a third fused source (see content_layer.py).
  - `search()`: a standalone combined lexical+vector RRF search, for direct
    use outside recall() (e.g. a future explicit "search my knowledge base"
    API/CLI). Not wired into recall() — kept for that future use.

Degrades to lexical-only when no vector backend/embedder is configured or
available (EmbeddingProvider.embed() returns None) — never raises.
"""
from __future__ import annotations

import logging

from .content_store import ContentStore, SearchResult
from .embedding_pipeline import EmbeddingPipeline
from .vector_backend import VectorBackend

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion constant — same family/value as ContentStore's own
# porter/trigram merge (RRF_K in content_store.py) and ContentLayerManager's
# lexical/dense merge (RECALL_RRF_K in content_layer.py).
RRF_K = 60


class HybridRetriever:
    """Combines ContentStore FTS5 search with VectorBackend cosine search."""

    def __init__(
        self,
        content_store: ContentStore,
        vector_backend: VectorBackend | None,
        embedding_pipeline: EmbeddingPipeline | None,
    ):
        self.content_store = content_store
        self.vector_backend = vector_backend
        self.embedding_pipeline = embedding_pipeline

    # ─── Vector-only leg (used by ContentLayerManager.recall) ──────────

    def vector_only_search(
        self,
        query: str,
        limit: int = 5,
        category: str | None = None,
    ) -> list[SearchResult]:
        """Embed `query`, search VectorBackend, and fetch full SearchResult
        rows for the hits.

        Returns [] (never raises) when: no vector backend/pipeline is
        configured, no embedder is available (offline / sentence-transformers
        not installed), the query embeds to None, or nothing matches.
        """
        if not query or not query.strip():
            return []
        if self.vector_backend is None or self.embedding_pipeline is None:
            return []

        try:
            query_vec = self.embedding_pipeline.embedding_provider.embed(query)
        except Exception:
            logger.warning("HybridRetriever: query embedding failed", exc_info=True)
            return []
        if query_vec is None:
            return []

        try:
            hits = self.vector_backend.search(query_vec, limit=limit)
        except Exception:
            logger.warning("HybridRetriever: vector_backend.search failed", exc_info=True)
            return []
        if not hits:
            return []

        # VectorBackend hits are keyed "chunk:<chunks.rowid>" (Task 2's
        # correction — see content_store._maybe_embed). Strip the prefix to
        # land back in the same rowid space chunks/chunks_trigram share.
        rowids: list[int] = []
        score_by_rowid: dict[int, float] = {}
        for hit in hits:
            hid = hit.get("id", "")
            if not isinstance(hid, str) or not hid.startswith("chunk:"):
                continue
            try:
                rowid = int(hid.split(":", 1)[1])
            except ValueError:
                continue
            rowids.append(rowid)
            score_by_rowid[rowid] = hit.get("score", 0.0)

        if not rowids:
            return []

        try:
            return self._fetch_by_rowid(rowids, score_by_rowid, category)[:limit]
        except Exception:
            logger.warning("HybridRetriever: chunk row fetch failed", exc_info=True)
            return []

    def _fetch_by_rowid(
        self,
        rowids: list[int],
        score_by_rowid: dict[int, float],
        category: str | None = None,
    ) -> list[SearchResult]:
        """Fetch full chunk rows for the given rowids, sorted by vector
        score descending. Optionally filtered by source_category — VectorBackend
        itself has no category awareness, so this is where a category filter
        for the dense leg is applied.
        """
        placeholders = ",".join("?" for _ in rowids)
        params: list = list(rowids)
        filter_clause = ""
        if category:
            filter_clause = " AND source_category = ?"
            params.append(category)

        rows = self.content_store.db.execute(
            f"""
            SELECT rowid, title, content, source_id, content_type,
                   source_category, session_id, timestamp
            FROM chunks
            WHERE rowid IN ({placeholders}){filter_clause}
            """,
            params,
        ).fetchall()

        row_by_id = {row["rowid"]: row for row in rows}
        ordered_rowids = sorted(
            (rid for rid in rowids if rid in row_by_id),
            key=lambda rid: -score_by_rowid.get(rid, 0.0),
        )

        results = []
        for rowid in ordered_rowids:
            row = row_by_id[rowid]
            results.append(SearchResult(
                rowid=row["rowid"],
                title=row["title"],
                content=row["content"],
                source_id=row["source_id"],
                content_type=row["content_type"],
                source_category=row["source_category"],
                session_id=row["session_id"],
                timestamp=row["timestamp"],
                rank=score_by_rowid.get(rowid, 0.0),
            ))
        return results

    # ─── Combined lexical+vector RRF (standalone use) ───────────────────

    def search(
        self,
        query: str,
        k: int = 5,
        category: str | None = None,
        alpha: float = 0.5,
    ) -> list[SearchResult]:
        """Standalone combined RRF search: FTS5 (lexical) + vector (dense).

        1. FTS5 search (ContentStore's own porter+trigram RRF) -> top k*3
        2. If a vector backend/embedder is available and alpha > 0: embed
           query, cosine search -> top k*3
        3. Weighted RRF merge of the two rankings on the shared rowid space
        4. Return top k

        Args:
            alpha: weight between lexical and vector (0.0 = lexical only,
                1.0 = vector only). Values in between blend the two RRF
                contributions per rowid.

        Falls back to pure FTS5 (today's ContentStore.search() behavior)
        when no vector backend is configured, no embedder is available, or
        alpha <= 0.0.
        """
        if not query or not query.strip():
            return []

        pool = max(k, k * 3)
        lexical = self.content_store.search(query, limit=pool, category=category)

        if alpha <= 0.0 or self.vector_backend is None or self.embedding_pipeline is None:
            return lexical[:k]

        vector = self.vector_only_search(query, limit=pool, category=category)
        if not vector:
            return lexical[:k]

        scores: dict[int, float] = {}
        row_lookup: dict[int, SearchResult] = {}

        for rank_0, r in enumerate(lexical):
            scores[r.rowid] = scores.get(r.rowid, 0.0) + (1.0 - alpha) / (RRF_K + rank_0 + 1)
            row_lookup.setdefault(r.rowid, r)

        for rank_0, r in enumerate(vector):
            scores[r.rowid] = scores.get(r.rowid, 0.0) + alpha / (RRF_K + rank_0 + 1)
            row_lookup.setdefault(r.rowid, r)

        ordered_rowids = sorted(scores, key=lambda rid: -scores[rid])

        merged = []
        for rowid in ordered_rowids[:k]:
            base = row_lookup[rowid]
            merged.append(SearchResult(
                rowid=base.rowid,
                title=base.title,
                content=base.content,
                source_id=base.source_id,
                content_type=base.content_type,
                source_category=base.source_category,
                session_id=base.session_id,
                timestamp=base.timestamp,
                rank=scores[rowid],
            ))
        return merged
