"""
ContentStore — FTS5 BM25 dual-index knowledge base.

Stores and searches content (reflections, perceptions, events, errors)
using SQLite FTS5 with two complementary tokenizers:
  - porter + unicode61: stemming-based search ("trading" finds "traded", "trades")
  - trigram: substring search ("51155" finds exact matches in logs)

Results from both indexes are merged via Reciprocal Rank Fusion (RRF).

Inspired by context-mode/src/store.ts — reimplemented 100% in Python.
No MCP, no Node.js, no external deps.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .constants import DEFAULT_DB_PATH
from .embedding_pipeline import EmbeddingPipeline
from .sqlite_tuning import tune
from .timeutil import naive_utcnow
from .vector_backend import HNSWBackend, SqliteVecBackend, VectorBackend

# Type alias for any vector backend (all share the same API)
VectorBackendType = VectorBackend | SqliteVecBackend | HNSWBackend

logger = logging.getLogger(__name__)

# ─── Data Classes ───────────────────────────────────────────────────────

@dataclass
class SearchResult:
    """A single search result from ContentStore."""
    rowid: int
    title: str
    content: str
    source_id: int
    content_type: str
    source_category: str
    session_id: str
    timestamp: str
    rank: float  # BM25 or RRF score

    def to_dict(self) -> dict:
        return {
            "rowid": self.rowid,
            "title": self.title,
            "content": self.content,
            "source_id": self.source_id,
            "content_type": self.content_type,
            "source_category": self.source_category,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "rank": round(self.rank, 4),
        }


@dataclass
class IndexResult:
    """Outcome of an index() call.

    status:
      "new"            — new source, chunks written
      "category_added" — content already known, chunks written for a new category
      "duplicate"      — nothing written (same content, same category)
    """
    source_id: int
    status: str
    chunks_added: int = 0

    @property
    def is_new_content(self) -> bool:
        return self.status != "duplicate"


@dataclass
class SourceInfo:
    """Metadata about a content source."""
    id: int
    label: str
    chunk_count: int
    indexed_at: str
    source_category: str
    content_hash: str | None = None


# ─── Constants ──────────────────────────────────────────────────────────

VALID_CATEGORIES = {"reflection", "perception", "trading", "system", "error", "consciousness", "external", "session", "pentest", "reference", "payload"}
VALID_CONTENT_TYPES = {"prose", "code", "metric", "log", "yaml", "markdown"}

# RRF constant (original paper uses k=60)
RRF_K = 60


# ─── ContentStore ───────────────────────────────────────────────────────

class ContentStore:
    """
    FTS5 BM25 dual-index knowledge base.

    All content is stored in SQLite FTS5 with two virtual tables:
    - chunks: porter+unicode61 tokenizer (stemming)
    - chunks_trigram: trigram tokenizer (substring match)

    Search merges results from both via Reciprocal Rank Fusion.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        vector_backend: VectorBackendType | None = None,
        embeddings: EmbeddingPipeline | None = None,
    ):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db = sqlite3.connect(str(self.db_path))
        tune(self.db, foreign_keys=True)
        self.db.row_factory = sqlite3.Row

        self._init_schema()

        # Optional vector-search side channel. Both default to None, which
        # preserves exact FTS5-only behavior for every existing caller.
        self.vector_backend = vector_backend
        self.embeddings = embeddings

        # Short-TTL query cache. The daemon repeats the same recall query
        # dozens of times per minute; without this each one page-scans the
        # FTS index + WAL (and with an oversized WAL that is 90-100% CPU).
        # A 30s TTL collapses the repeats while still letting fresh content
        # surface on the next cadence. Keyed by (query, limit, filter fields).
        self._search_cache: dict[tuple, tuple[float, list]] = {}
        self._search_cache_ttl_s = 30.0

    # ─── Query cache helpers ──────────────────────────────────────

    def _cache_key(self, query: str, limit: int, category: str | None,
                   content_type: str | None, since: str | None,
                   include_stale: bool, use_trigram: bool) -> tuple:
        return (query.strip().lower(), limit, category, content_type,
                since, include_stale, use_trigram)

    def _cache_get(self, key: tuple):
        hit = self._search_cache.get(key)
        if hit is None:
            return None
        ts, value = hit
        if time.time() - ts > self._search_cache_ttl_s:
            self._search_cache.pop(key, None)
            return None
        return value

    def _cache_put(self, key: tuple, value: list) -> None:
        # Bound the cache; simple cap avoids unbounded growth over a long run.
        if len(self._search_cache) >= 256:
            self._search_cache.clear()
        self._search_cache[key] = (time.time(), value)

    def _cache_invalidate(self) -> None:
        """Drop cached results after index/delete/clear so reads reflect writes."""
        self._search_cache.clear()

    # ─── Schema ──────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        """Initialize all tables and indexes."""
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                indexed_at TEXT NOT NULL DEFAULT (datetime('now')),
                source_category TEXT,
                content_hash TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                title,
                content,
                source_id UNINDEXED,
                content_type UNINDEXED,
                source_category UNINDEXED,
                session_id UNINDEXED,
                timestamp UNINDEXED,
                tokenize='porter unicode61'
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_trigram USING fts5(
                title,
                content,
                source_id UNINDEXED,
                content_type UNINDEXED,
                source_category UNINDEXED,
                session_id UNINDEXED,
                timestamp UNINDEXED,
                tokenize='trigram'
            );

            CREATE INDEX IF NOT EXISTS idx_sources_label ON sources(label);
            CREATE INDEX IF NOT EXISTS idx_sources_category ON sources(source_category);

            CREATE TABLE IF NOT EXISTS source_tombstones (
                source_id INTEGER PRIMARY KEY,
                reason TEXT NOT NULL DEFAULT 'content_changed',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (source_id) REFERENCES sources(id)
            );
        """)
        self.db.commit()

    # ─── Indexing ────────────────────────────────────────────────────

    def index(
        self,
        label: str,
        content: str,
        category: str,
        content_type: str = "prose",
        session_id: str = "",
        chunk_size: int = 2000,
        overlap: float = 0.0,
    ) -> int:
        """Index content into FTS5 — see index_ex(); returns the source_id."""
        return self.index_ex(
            label, content, category,
            content_type=content_type, session_id=session_id,
            chunk_size=chunk_size, overlap=overlap,
        ).source_id

    def index_ex(
        self,
        label: str,
        content: str,
        category: str,
        content_type: str = "prose",
        session_id: str = "",
        chunk_size: int = 2000,
        overlap: float = 0.0,
    ) -> IndexResult:
        """
        Index content into FTS5 (porter + trigram).

        Long content is split into chunks at semantic boundaries (YAML,
        markdown headings, or paragraph boundaries) for better search
        granularity. Adjacent chunks may overlap by a configurable amount.

        Args:
            label: Human-readable source label (e.g., "reflection_2026-06-04")
            content: Text content to index
            category: One of VALID_CATEGORIES
            content_type: One of VALID_CONTENT_TYPES
            session_id: Optional session identifier
            chunk_size: Max chars per chunk
            overlap: Fraction of chunk_size to overlap between chunks (default 0.0 = none; new callers can pass 0.2 for 20%)

        Returns:
            IndexResult(source_id, status, chunks_added) — `status` tells a
            caller whether real work happened ("new" / "category_added") or the
            content was already indexed ("duplicate"). Without it, an ingest
            summary counts a full re-run of an unchanged corpus as "14000
            ingested", which is exactly the number used as evidence for CS1.
        """
        if category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{category}'. Must be one of: {VALID_CATEGORIES}")
        if content_type not in VALID_CONTENT_TYPES:
            raise ValueError(f"Invalid content_type '{content_type}'. Must be one of: {VALID_CONTENT_TYPES}")

        content_hash = hashlib.sha256(content.encode()).hexdigest()
        timestamp = naive_utcnow().isoformat()

        # Tombstone check: if label exists with a DIFFERENT hash, mark old source stale
        old_sources = self.db.execute(
            "SELECT id, content_hash FROM sources WHERE label = ?",
            (label,),
        ).fetchall()
        for old in old_sources:
            if old["content_hash"] != content_hash:
                self._mark_stale(int(old["id"]), "content_changed")

        # Check for duplicate content (same hash = source already indexed)
        existing = self.db.execute(
            "SELECT id, label FROM sources WHERE content_hash = ? LIMIT 1",
            (content_hash,),
        ).fetchone()
        if existing:
            source_id = int(existing["id"])
            # R-05: ensure chunks exist for THIS category so a filtered search
            # finds the text. label stays first-seen provenance. session_id is
            # recorded on the new chunks but is NOT a dedup key or a search axis
            # in v2.0.1 (search() filters by category, not session).
            already = self.db.execute(
                "SELECT 1 FROM chunks WHERE source_id = ? AND source_category = ?"
                " LIMIT 1",
                (source_id, category),
            ).fetchone()
            if already:
                return IndexResult(source_id, "duplicate", 0)
            label = existing["label"]
            chunks = self._chunk_content(content, chunk_size=chunk_size, overlap=overlap, content_type=content_type)
            pending: list[tuple[str, str]] = []
            for i, chunk in enumerate(chunks):
                title = (f"{label}" if len(chunks) == 1
                         else f"{label} [part {i+1}/{len(chunks)}]")
                # Insert into porter FTS5
                cursor = self.db.execute(
                    "INSERT INTO chunks (title, content, source_id,"
                    " content_type, source_category, session_id, timestamp)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (title, chunk, source_id, content_type, category,
                     session_id, timestamp))
                chunk_rowid = cursor.lastrowid
                # Insert into trigram FTS5 (main DB pre-rebuild, or separate DB post-rebuild)
                self._insert_trigram(title, chunk, chunk_rowid, source_id, content_type, category, session_id, timestamp)
                if chunk_rowid is not None:
                    pending.append((f"chunk:{chunk_rowid}", chunk))
            self.db.commit()
            self._cache_invalidate()            # new content must surface
            self._maybe_embed_batch(pending, category)
            return IndexResult(source_id, "category_added", len(chunks))

        # Create source record
        cursor = self.db.execute(
            "INSERT INTO sources (label, source_category, content_hash) VALUES (?, ?, ?)",
            (label, category, content_hash),
        )
        source_id = int(cursor.lastrowid or 0)

        # Split into chunks at semantic boundaries (YAML, headings, or paragraphs)
        chunks = self._chunk_content(content, chunk_size=chunk_size, overlap=overlap, content_type=content_type)

        pending = []
        for i, chunk in enumerate(chunks):
            title = f"{label}" if len(chunks) == 1 else f"{label} [part {i+1}/{len(chunks)}]"
            # Insert into porter FTS5 (always in main DB)
            cursor = self.db.execute(
                "INSERT INTO chunks (title, content, source_id, content_type, source_category, session_id, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, chunk, source_id, content_type, category, session_id, timestamp),
            )
            chunk_rowid = cursor.lastrowid
            # Insert into trigram FTS5 (main DB pre-rebuild, or separate DB post-rebuild)
            self._insert_trigram(title, chunk, chunk_rowid, source_id, content_type, category, session_id, timestamp)
            if chunk_rowid is not None:
                pending.append((f"chunk:{chunk_rowid}", chunk))

        # Update chunk count
        self.db.execute(
            "UPDATE sources SET chunk_count = ? WHERE id = ?",
            (len(chunks), source_id),
        )
        self.db.commit()
        self._cache_invalidate()                # new content must surface

        # Embedding runs AFTER the FTS5 commit, in one batch: FTS5 is the
        # primary, always-on path and must not be held open (nor rolled back)
        # by a slow model call.
        self._maybe_embed_batch(pending, category)

        return IndexResult(source_id, "new", len(chunks))

    def _maybe_embed_batch(
        self, pending: list[tuple[str, str]], category: str | None = None
    ) -> None:
        """Embed a document's chunks into the vector backend, if configured.

        One call per document, not per chunk: EmbeddingPipeline.embed_batch
        does a single model invocation for all texts and a single vector-store
        transaction. The previous per-chunk path paid a model call AND an
        fsync-per-vector, which is the dominant cost at corpus scale.

        Keyed by the `chunks` FTS5 table's own rowid (not source_id — a
        source can have many chunks, and VectorBackend.add() is INSERT OR
        REPLACE keyed by id, so keying on source_id would silently collapse
        every chunk of a document onto a single vector). Using chunk_rowid
        also means a vector search hit maps directly onto the same
        `chunks.rowid` that `_rrf_merge()` already fetches full rows by.
        `category` is denormalized onto the vector row so a category-scoped
        recall can pre-filter candidates in SQL.

        Best-effort: embedding/storage failures are logged and swallowed so
        they never interrupt FTS5 ingestion, which is the primary, always-on
        path.
        """
        if not pending or self.vector_backend is None or self.embeddings is None:
            return
        try:
            self.embeddings.embed_batch(pending, category=category)
        except Exception as e:
            logger.warning(
                f"ContentStore: embedding failed for {len(pending)} chunk(s) "
                f"of category {category!r}: {e}"
            )

    def _chunk_by_headings(self, text: str, chunk_size: int = 2000, overlap: float = 0.2) -> list[str]:
        r"""
        Split markdown content by heading boundaries.

        Splits at lines matching ^#{1,3}\s (h1, h2, h3). Each chunk starts
        with a heading and includes content until the next heading or end.
        If a chunk exceeds chunk_size, it's split at paragraph boundaries.
        Always returns at least as many chunks as there are headings.

        Args:
            text: Markdown content
            chunk_size: Max chars per chunk
            overlap: Fraction of chunk_size to overlap (0.0–1.0)

        Returns:
            List of chunks with overlap applied
        """
        heading_pattern = re.compile(r"^#{1,3}\s", re.MULTILINE)
        heading_positions = [m.start() for m in heading_pattern.finditer(text)]

        if not heading_positions:
            # No headings found, fall back to paragraph chunking
            return self._chunk_paragraphs(text, chunk_size, overlap)

        # Split on heading boundaries
        raw_chunks = []
        for i, pos in enumerate(heading_positions):
            if i + 1 < len(heading_positions):
                chunk = text[pos:heading_positions[i + 1]]
            else:
                chunk = text[pos:]
            if chunk.strip():
                raw_chunks.append(chunk.strip())

        # If any chunk exceeds chunk_size, split at paragraphs
        final_chunks = []
        for chunk in raw_chunks:
            if len(chunk) <= chunk_size:
                final_chunks.append(chunk)
            else:
                # Split by paragraphs within this heading section
                # Use paragraph-only split, don't apply overlap here (we'll do it at the end)
                subchunks = self._split_paragraphs_hard(chunk, chunk_size)
                final_chunks.extend(subchunks)

        # Apply overlap
        return self._apply_overlap(final_chunks, chunk_size, overlap)

    def _split_paragraphs_hard(self, text: str, chunk_size: int) -> list[str]:
        """
        Split text at paragraph boundaries without overlap.
        Internal helper for _chunk_by_headings to split large sections.
        """
        chunk_size = max(1, chunk_size)
        if len(text) <= chunk_size:
            return [text]

        raw_chunks = []
        remaining = text

        while remaining:
            if len(remaining) <= chunk_size:
                raw_chunks.append(remaining)
                break

            # Find last paragraph break within chunk_size
            split_at = remaining[:chunk_size].rfind("\n\n")

            if split_at == -1:
                # No paragraph break found — hard split at chunk_size
                split_at = chunk_size
            else:
                split_at += 2  # Include the \n\n

            raw_chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()

            if not remaining:
                break

        return [c for c in raw_chunks if c]

    def _chunk_yaml(self, text: str, chunk_size: int = 2000, overlap: float = 0.2) -> list[str]:
        """
        Split YAML content by document boundaries (lines that are exactly '---').

        Each chunk is one YAML document. If a document exceeds chunk_size,
        it's split at line boundaries.

        Args:
            text: YAML content
            chunk_size: Max chars per chunk
            overlap: Fraction of chunk_size to overlap

        Returns:
            List of chunks with overlap applied
        """
        # Split on lines that are exactly '---'
        lines = text.split('\n')
        raw_chunks = []
        current_chunk_lines = []

        for line in lines:
            if line.strip() == '---':
                if current_chunk_lines:
                    chunk_text = '\n'.join(current_chunk_lines).strip()
                    if chunk_text:
                        raw_chunks.append(chunk_text)
                    current_chunk_lines = []
            else:
                current_chunk_lines.append(line)

        # Don't forget the last chunk if it exists
        if current_chunk_lines:
            chunk_text = '\n'.join(current_chunk_lines).strip()
            if chunk_text:
                raw_chunks.append(chunk_text)

        # If chunks are too large, split by lines
        final_chunks = []
        for chunk in raw_chunks:
            if len(chunk) <= chunk_size:
                final_chunks.append(chunk)
            else:
                # Split by line boundaries
                chunk_lines = chunk.split('\n')
                subchunk_lines = []
                for line in chunk_lines:
                    subchunk_lines.append(line)
                    subchunk_text = '\n'.join(subchunk_lines)
                    if len(subchunk_text) > chunk_size and len(subchunk_lines) > 1:
                        # Commit the subchunk without this line
                        subchunk_lines.pop()
                        final_chunks.append('\n'.join(subchunk_lines))
                        subchunk_lines = [line]

                # Commit remaining lines
                if subchunk_lines:
                    final_chunks.append('\n'.join(subchunk_lines))

        # Apply overlap
        return self._apply_overlap(final_chunks, chunk_size, overlap)

    def _chunk_paragraphs(self, text: str, chunk_size: int = 2000, overlap: float = 0.2) -> list[str]:
        """
        Split content at paragraph boundaries (\\n\\n).

        Each chunk is at most chunk_size characters. Splits at the last
        paragraph break before the limit. If no paragraph breaks exist,
        performs hard split at chunk_size.

        Args:
            text: Content to chunk
            chunk_size: Max chars per chunk
            overlap: Fraction of chunk_size to overlap

        Returns:
            List of chunks with overlap applied
        """
        # B-009: chunk_size<=0 makes the slice below never shrink `remaining`
        chunk_size = max(1, chunk_size)

        # If content is small, return as-is
        if len(text) <= chunk_size:
            return [text]

        # Check if there are paragraph breaks
        if "\n\n" in text:
            # Split on paragraph breaks
            paragraphs = text.split("\n\n")
            raw_chunks = []
            current_chunk_parts = []
            current_chunk_size = 0

            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue

                # If adding this paragraph would exceed chunk_size, save current chunk and start new one
                para_size = len(para)
                separator_size = 2 if current_chunk_parts else 0  # "\n\n" between paragraphs

                if current_chunk_size + para_size + separator_size > chunk_size and current_chunk_parts:
                    # Save current chunk
                    raw_chunks.append("\n\n".join(current_chunk_parts))
                    current_chunk_parts = [para]
                    current_chunk_size = para_size
                else:
                    current_chunk_parts.append(para)
                    current_chunk_size += para_size + separator_size

            # Don't forget the last chunk
            if current_chunk_parts:
                raw_chunks.append("\n\n".join(current_chunk_parts))

            raw_chunks = [c for c in raw_chunks if c]  # Remove empty chunks
        else:
            # No paragraph breaks — hard split at chunk_size
            raw_chunks = []
            remaining = text

            while remaining:
                if len(remaining) <= chunk_size:
                    raw_chunks.append(remaining)
                    break
                else:
                    raw_chunks.append(remaining[:chunk_size])
                    remaining = remaining[chunk_size:]

        # Apply overlap
        return self._apply_overlap(raw_chunks, chunk_size, overlap)

    def _apply_overlap(self, chunks: list[str], chunk_size: int, overlap: float) -> list[str]:
        """
        Apply overlap between chunks by prepending the end of the previous chunk.

        Args:
            chunks: List of raw (non-overlapped) chunks
            chunk_size: Reference chunk size (used to calculate overlap amount)
            overlap: Fraction of chunk_size to overlap (0.0–1.0)

        Returns:
            List of chunks with overlap applied
        """
        if not chunks or overlap <= 0.0 or len(chunks) <= 1:
            return chunks

        overlap_amount = max(1, int(chunk_size * overlap))
        result = [chunks[0]]

        for i in range(1, len(chunks)):
            prev_chunk = chunks[i - 1]
            curr_chunk = chunks[i]

            # Take the last overlap_amount characters from the previous chunk
            if len(prev_chunk) > overlap_amount:
                overlap_text = prev_chunk[-overlap_amount:]
            else:
                # If previous chunk is smaller than overlap, use all of it
                overlap_text = prev_chunk

            # Prepend overlap to current chunk, but don't let it completely replace it
            if len(overlap_text) < len(curr_chunk):
                overlapped = overlap_text + "\n\n" + curr_chunk
            else:
                overlapped = curr_chunk

            result.append(overlapped)

        return result

    def _chunk_content(
        self,
        content: str,
        chunk_size: int = 2000,
        overlap: float = 0.0,
        content_type: str = "prose",
    ) -> list[str]:
        """
        Split content into chunks using semantic boundaries.

        Dispatch strategy (based on content_type only):
        1. If content_type == "yaml": split on YAML document boundaries
        2. Else if content_type == "markdown": split on markdown heading boundaries
        3. Else (all other types including "prose", "code", "metric", "log"):
           split on paragraph boundaries for backward compatibility

        Overlap is applied across all strategies.

        Args:
            content: Text content to chunk
            chunk_size: Max chars per chunk (default 2000)
            overlap: Fraction of chunk_size to overlap between chunks (default 0.0 = none)
            content_type: Type of content, determines chunking strategy

        Returns:
            List of chunks
        """
        chunk_size = max(1, chunk_size)

        # Handle empty content (backward compatibility: return [""])
        if not content:
            return [""]

        # Semantic dispatch based on content_type only
        if content_type == "yaml":
            return self._chunk_yaml(content, chunk_size, overlap)

        if content_type == "markdown":
            return self._chunk_by_headings(content, chunk_size, overlap)

        # Fallback for all other content_type values: paragraph boundaries (backward compatible)
        return self._chunk_paragraphs(content, chunk_size, overlap)

    # ─── Search ──────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 5,
        category: str | None = None,
        content_type: str | None = None,
        since: str | None = None,
        include_stale: bool = False,
        use_trigram: bool = False,
    ) -> list[SearchResult]:
        """
        Search content using BM25 with dual-index RRF merge.

        1. Search porter index (stemming) — good for conceptual queries
        2. Search trigram index (substring) — good for exact matches
        3. Merge via Reciprocal Rank Fusion

        Args:
            query: Search query
            limit: Max results to return
            category: Filter by source category
            content_type: Filter by content type
            since: ISO timestamp — only results after this time
            include_stale: If True, include chunks from tombstoned sources
            use_trigram: If True, include trigram index in search (exact match).
                If False (default), porter-only search. Auto-detect overrides
                this to True when the query pattern suggests substring search
                (code, file paths, IDs like "T1569.002").

        Returns:
            List of SearchResult sorted by RRF score (descending)
        """
        if not query.strip():
            return []

        # Auto-detect: queries with dots, slashes, dashes, underscores, or
        # dotted numbers suggest code/identifiers — activate trigram.
        if not use_trigram and self._query_needs_trigram(query):
            use_trigram = True

        # Short-TTL cache: the daemon repeats identical recall queries
        # dozens of times per minute; a hit skips both FTS scans entirely.
        cache_key = self._cache_key(
            query, limit, category, content_type, since, include_stale,
            use_trigram)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        # Build WHERE clause for filters
        filter_clause = ""
        filter_params: list = []

        if not include_stale:
            filter_clause += " AND source_id NOT IN (SELECT source_id FROM source_tombstones)"
        if category:
            filter_clause += " AND source_category = ?"
            filter_params.append(category)
        if content_type:
            filter_clause += " AND content_type = ?"
            filter_params.append(content_type)
        if since:
            filter_clause += " AND timestamp >= ?"
            filter_params.append(since)

        # Porter search (BM25)
        porter_results = self._fts_search(
            "chunks", query, limit * 3, filter_clause, filter_params
        )

        if not use_trigram:
            # Porter-only: wrap in RRF with empty trigram for consistent shape
            result = self._rrf_merge(porter_results, [])[:limit]
            self._cache_put(cache_key, result)
            return result

        # Trigram search (BM25) — from separate DB or fallback to main DB
        trigram_results = self._fts_search_trigram(
            query, limit * 3, filter_clause, filter_params
        )

        # Merge via RRF
        merged = self._rrf_merge(porter_results, trigram_results)
        result = merged[:limit]
        self._cache_put(cache_key, result)
        return result

    _TRIGGER_PATTERN_RE = re.compile(
        r"[./\\_\-]"           # dots, slashes, backslashes, underscores, dashes
        r"|\d+\.\d+"           # dotted numbers (e.g., T1569.002, CVE-2024-1234)
        r"|\d{4,}",            # 4+ consecutive digits (log IDs, hashes)
    )

    def _query_needs_trigram(self, query: str) -> bool:
        """Heuristic: detect if query benefits from trigram (substring) search."""
        return bool(self._TRIGGER_PATTERN_RE.search(query))

    def _fts_search(
        self,
        table: str,
        query: str,
        limit: int,
        filter_clause: str,
        filter_params: list,
    ) -> list[tuple[int, float]]:
        """
        Execute FTS5 BM25 search on a single table.

        Returns list of (rowid, bm25_score) sorted by score descending.
        """
        # Escape special FTS5 characters in query
        escaped = self._escape_fts_query(query, table)

        if not escaped:
            return []

        try:
            rows = self.db.execute(
                f"""
                SELECT rowid, bm25({table}) as score
                FROM {table}
                WHERE {table} MATCH ?{filter_clause}
                ORDER BY score
                LIMIT ?
                """,
                [escaped] + filter_params + [limit],
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS5 match syntax error — fallback to simple token search
            try:
                simple_query = " OR ".join(f'"{w}"' for w in query.split()[:5])
                rows = self.db.execute(
                    f"""
                    SELECT rowid, bm25({table}) as score
                    FROM {table}
                    WHERE {table} MATCH ?{filter_clause}
                    ORDER BY score
                    LIMIT ?
                    """,
                    [simple_query] + filter_params + [limit],
                ).fetchall()
            except sqlite3.OperationalError:
                return []

        # bm25() returns negative scores (more negative = better match)
        # Convert to positive for RRF (lower bm25 = higher rank)
        return [(row["rowid"], row["score"]) for row in rows]

    def _fts_search_trigram(
        self,
        query: str,
        limit: int,
        filter_clause: str,
        filter_params: list,
    ) -> list[tuple[int, float]]:
        """
        Execute trigram FTS5 BM25 search, using a separate DB if available.

        Resolution order:
        1. If `conscio_trigram.db` exists alongside the main DB, search there.
        2. If it does not exist, fall back to `chunks_trigram` in the main DB
           (pre-rebuild state — transparent backward compatibility).
        3. If neither has a `chunks_trigram` table, return [] (no trigram
           index available at all).

        Note: filter_clause may reference source_tombstones which only exists
        in the main DB. When searching the separate trigram DB, we strip the
        tombstone filter and apply it post-query against the main DB instead.
        """
        trigram_db_path = self.db_path.parent / "conscio_trigram.db"

        # Split filter clause: tombstone filter stays in main DB, rest goes to trigram
        tombstone_filter = ""
        safe_clause = filter_clause
        if "source_tombstones" in filter_clause:
            # Extract tombstone sub-clause for post-filter
            tombstone_filter = " AND source_id NOT IN (SELECT source_id FROM source_tombstones)"
            safe_clause = filter_clause.replace(
                " AND source_id NOT IN (SELECT source_id FROM source_tombstones)", ""
            )

        # Try separate trigram DB first
        if trigram_db_path.exists():
            try:
                conn = sqlite3.connect(str(trigram_db_path))
                conn.row_factory = sqlite3.Row
                try:
                    escaped = self._escape_fts_query(query, "chunks_trigram")
                    if not escaped:
                        return []
                    rows = conn.execute(
                        f"""
                        SELECT rowid, bm25(chunks_trigram) as score
                        FROM chunks_trigram
                        WHERE chunks_trigram MATCH ?{safe_clause}
                        ORDER BY score
                        LIMIT ?
                        """,
                        [escaped] + filter_params + [limit],
                    ).fetchall()
                    results = [(row["rowid"], row["score"]) for row in rows]
                    # Post-filter tombstones against main DB
                    if tombstone_filter:
                        tombed_ids = self._get_tombstoned_source_ids()
                        if tombed_ids:
                            results = [r for r in results if r[0] not in tombed_ids]
                    return results
                finally:
                    conn.close()
            except sqlite3.OperationalError:
                pass  # fall through to main DB

        # Fallback: use chunks_trigram from main DB (pre-rebuild state)
        try:
            table_exists = self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_trigram'"
            ).fetchone()
            if not table_exists:
                return []
            return self._fts_search(
                "chunks_trigram", query, limit, filter_clause, filter_params
            )
        except sqlite3.OperationalError:
            return []

    def _get_tombstoned_source_ids(self) -> set[int]:
        """Return set of source_ids that are tombstoned (empty if table missing)."""
        try:
            rows = self.db.execute(
                "SELECT source_id FROM source_tombstones"
            ).fetchall()
            return {row["source_id"] for row in rows}
        except sqlite3.OperationalError:
            return set()

    # ─── Rebuild / Migration ──────────────────────────────────────────

    def _insert_trigram(
        self, title: str, content: str, rowid: int | None,
        source_id: int, content_type: str, category: str,
        session_id: str | None, timestamp: str,
    ) -> None:
        """
        Insert a chunk into the trigram FTS5 index.

        Post-rebuild: insert into conscio_trigram.db with explicit rowid
        (keeping rowid parity with chunks table for RRF merge).
        Pre-rebuild: insert into chunks_trigram in main DB (original behavior).
        """
        trigram_db_path = self.db_path.parent / "conscio_trigram.db"

        if trigram_db_path.exists():
            try:
                conn = sqlite3.connect(str(trigram_db_path))
                try:
                    conn.execute(
                        "INSERT INTO chunks_trigram (rowid, title, content, source_id, content_type, source_category, session_id, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (rowid, title, content, source_id, content_type, category, session_id, timestamp),
                    )
                    conn.commit()
                    return
                finally:
                    conn.close()
            except sqlite3.OperationalError:
                pass  # fall through to main DB

        # Fallback: chunks_trigram in main DB (pre-rebuild)
        try:
            self.db.execute(
                "INSERT INTO chunks_trigram (title, content, source_id, content_type, source_category, session_id, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, content, source_id, content_type, category, session_id, timestamp),
            )
        except sqlite3.OperationalError:
            pass  # table doesn't exist — skip

    def rebuild_db(self) -> dict:
        """
        Migrate trigram index from main DB to a separate conscio_trigram.db.

        Steps:
        1. Check if chunks_trigram exists in main DB (idempotent — skip if not)
        2. Backup conscio.db → conscio.db.bak
        3. Create conscio_trigram.db with chunks_trigram schema
        4. Copy all chunk data (with explicit rowids) to trigram DB
        5. DROP chunks_trigram from main DB
        6. VACUUM main DB (recovers ~230MB)
        7. If any step fails, restore from backup

        Returns:
            Dict with migration stats: {migrated, db_size_before, db_size_after, trigram_size}
        """
        import shutil

        # Idempotency: if chunks_trigram not in main DB, already migrated
        has_trigram = self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_trigram'"
        ).fetchone()
        if not has_trigram:
            logger.info("rebuild_db: chunks_trigram already migrated, skipping")
            return {"migrated": 0, "status": "already_migrated"}

        # Count chunks to migrate
        chunk_count = self.db.execute(
            "SELECT COUNT(*) as c FROM chunks_trigram"
        ).fetchone()["c"]

        # Backup main DB
        backup_path = self.db_path.with_suffix(".db.bak")
        self.db.close()
        shutil.copy2(str(self.db_path), str(backup_path))
        self.db = sqlite3.connect(str(self.db_path))
        tune(self.db, foreign_keys=True)
        self.db.row_factory = sqlite3.Row

        size_before = self.db_path.stat().st_size

        try:
            # Create trigram DB
            trigram_db_path = self.db_path.parent / "conscio_trigram.db"
            if trigram_db_path.exists():
                trigram_db_path.unlink()

            tri_conn = sqlite3.connect(str(trigram_db_path))
            tri_conn.row_factory = sqlite3.Row
            tune(tri_conn)
            tri_conn.execute("""
                CREATE VIRTUAL TABLE chunks_trigram USING fts5(
                    title,
                    content,
                    source_id UNINDEXED,
                    content_type UNINDEXED,
                    source_category UNINDEXED,
                    session_id UNINDEXED,
                    timestamp UNINDEXED,
                    tokenize='trigram'
                )
            """)
            tri_conn.commit()

            # Copy chunks with explicit rowids using cursor (avoids OFFSET O(n²))
            batch_size = 1000
            last_rowid = -1
            while True:
                rows = self.db.execute(
                    """
                    SELECT rowid, title, content, source_id, content_type,
                           source_category, session_id, timestamp
                    FROM chunks_trigram
                    WHERE rowid > ?
                    ORDER BY rowid
                    LIMIT ?
                    """,
                    [last_rowid, batch_size],
                ).fetchall()
                if not rows:
                    break
                placeholders = ",".join(["(?,?,?,?,?,?,?,?)"] * len(rows))
                values = []
                for row in rows:
                    values.extend([
                        row["rowid"], row["title"], row["content"],
                        row["source_id"], row["content_type"],
                        row["source_category"], row["session_id"], row["timestamp"]
                    ])
                    last_rowid = row["rowid"]
                tri_conn.execute(
                    f"INSERT INTO chunks_trigram(rowid, title, content, source_id, "
                    f"content_type, source_category, session_id, timestamp) "
                    f"VALUES {placeholders}",
                    values
                )
                tri_conn.commit()

            tri_conn.close()

            # Drop trigram from main DB
            self.db.execute("DROP TABLE chunks_trigram")
            self.db.commit()
            self.db.execute("VACUUM")
            self.db.commit()

            size_after = self.db_path.stat().st_size
            trigram_size = trigram_db_path.stat().st_size

            logger.info(
                f"rebuild_db: migrated {chunk_count} chunks, "
                f"main DB {size_before/1048576:.1f}MB → {size_after/1048576:.1f}MB, "
                f"trigram DB {trigram_size/1048576:.1f}MB"
            )

            return {
                "migrated": chunk_count,
                "db_size_before": size_before,
                "db_size_after": size_after,
                "trigram_size": trigram_size,
                "status": "ok",
            }

        except Exception as e:
            # Rollback: restore from backup
            logger.error(f"rebuild_db failed: {e}, restoring from backup")
            self.db.close()
            shutil.copy2(str(backup_path), str(self.db_path))
            # Remove partial trigram DB
            trigram_db_path = self.db_path.parent / "conscio_trigram.db"
            if trigram_db_path.exists():
                trigram_db_path.unlink()
            self.db = sqlite3.connect(str(self.db_path))
            tune(self.db, foreign_keys=True)
            self.db.row_factory = sqlite3.Row
            raise

    def _escape_fts_query(self, query: str, table: str) -> str:
        """
        Escape and format query for FTS5 MATCH.

        Porter: uses standard FTS5 query syntax (AND, OR, phrases)
        Trigram: wraps entire query as a single phrase for substring match
        """
        # Remove FTS5 special characters that break MATCH
        cleaned = query.replace('"', '').replace("'", "").replace("*", "")

        if not cleaned.strip():
            return ""

        if table == "chunks_trigram":
            # Trigram: exact substring match — wrap entire query as phrase
            return f'"{cleaned}"'
        else:
            # Porter: token-based search with OR for broader recall
            # FTS5 implicit AND means multi-term queries miss docs that
            # don't contain *every* term. Using OR gives better recall,
            # and BM25 ranking still prioritises docs with more matches.
            tokens = cleaned.split()
            if len(tokens) > 10:
                tokens = tokens[:10] # Limit query complexity
            return " OR ".join(f'"{t}"' for t in tokens)

    def _rrf_merge(
        self,
        porter: list[tuple[int, float]],
        trigram: list[tuple[int, float]],
    ) -> list[SearchResult]:
        """
        Merge results from porter and trigram indexes using RRF.

        RRF score = 1/(k + rank_porter) + 1/(k + rank_trigram)
        where k = RRF_K (default 60).

        This gives a balanced merge that doesn't require score normalization.
        """
        rrf_scores: dict[int, float] = {}

        # Porter contributions
        for rank_0, (rowid, _score) in enumerate(porter):
            rrf_scores[rowid] = rrf_scores.get(rowid, 0.0) + 1.0 / (RRF_K + rank_0 + 1)

        # Trigram contributions
        for rank_0, (rowid, _score) in enumerate(trigram):
            rrf_scores[rowid] = rrf_scores.get(rowid, 0.0) + 1.0 / (RRF_K + rank_0 + 1)

        # Sort by RRF score descending
        sorted_rowids = sorted(rrf_scores.keys(), key=lambda r: rrf_scores[r], reverse=True)

        if not sorted_rowids:
            return []

        # Fetch full row data in a single query (avoids N+1)
        placeholders = ",".join("?" for _ in sorted_rowids)
        rows = self.db.execute(
            f"""
            SELECT c.rowid, c.title, c.content, c.source_id,
                   c.content_type, c.source_category, c.session_id, c.timestamp
            FROM chunks c
            WHERE c.rowid IN ({placeholders})
            """,
            sorted_rowids,
        ).fetchall()

        # Preserve RRF sort order
        row_by_id = {row["rowid"]: row for row in rows}
        results = []
        for rowid in sorted_rowids:
            row = row_by_id.get(rowid)
            if row:
                results.append(SearchResult(
                    rowid=row["rowid"],
                    title=row["title"],
                    content=row["content"],
                    source_id=row["source_id"],
                    content_type=row["content_type"],
                    source_category=row["source_category"],
                    session_id=row["session_id"],
                    timestamp=row["timestamp"],
                    rank=rrf_scores[rowid],
                ))

        return results

    # ─── Retrieval ───────────────────────────────────────────────────

    def get_by_source(self, source_id: int) -> list[SearchResult]:
        """Get all chunks for a given source."""
        rows = self.db.execute(
            """
            SELECT rowid, title, content, source_id,
                   content_type, source_category, session_id, timestamp
            FROM chunks
            WHERE source_id = ?
            ORDER BY rowid
            """,
            (source_id,),
        ).fetchall()

        return [
            SearchResult(
                rowid=r["rowid"], title=r["title"], content=r["content"],
                source_id=r["source_id"], content_type=r["content_type"],
                source_category=r["source_category"], session_id=r["session_id"],
                timestamp=r["timestamp"], rank=0.0,
            )
            for r in rows
        ]

    def get_source(self, source_id: int) -> SourceInfo | None:
        """Get source metadata."""
        row = self.db.execute(
            "SELECT id, label, chunk_count, indexed_at, source_category, content_hash FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()

        if not row:
            return None

        return SourceInfo(
            id=row["id"], label=row["label"], chunk_count=row["chunk_count"],
            indexed_at=row["indexed_at"], source_category=row["source_category"],
            content_hash=row["content_hash"],
        )

    # ─── Maintenance ─────────────────────────────────────────────────

    def delete_source(self, source_id: int) -> bool:
        """Delete a source and all its chunks from both FTS5 tables."""
        source = self.get_source(source_id)
        if not source:
            return False

        for table in ("chunks", "chunks_trigram"):
            self.db.execute(f"DELETE FROM {table} WHERE source_id = ?", (source_id,))

        # v3.9.4: the tombstone goes FIRST. It carries a FOREIGN KEY onto
        # sources(id), so deleting the source while one exists raised
        # IntegrityError — and this is the path compact() takes, so one
        # tombstoned source aborted a whole compaction run.
        self.db.execute(
            "DELETE FROM source_tombstones WHERE source_id = ?", (source_id,))
        self.db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        self.db.commit()
        self._cache_invalidate()                # deleted content must go away
        return True

    def compact(self, before_days: int = 90) -> int:
        """
        Compact old content: remove sources older than before_days.

        Returns the number of sources removed.
        """
        from datetime import timedelta
        cutoff = (naive_utcnow() - timedelta(days=before_days)).isoformat()

        old_sources = self.db.execute(
            "SELECT id FROM sources WHERE indexed_at < ?",
            (cutoff,),
        ).fetchall()

        if not old_sources:
            return 0

        source_ids = [row["id"] for row in old_sources]
        placeholders = ",".join("?" for _ in source_ids)

        # Batch delete in single transaction: 4 DELETEs + 1 commit. Tombstones
        # go before sources — they hold a FOREIGN KEY onto sources(id), and one
        # tombstoned source used to abort the entire compaction (v3.9.4).
        for table in ("chunks", "chunks_trigram"):
            self.db.execute(f"DELETE FROM {table} WHERE source_id IN ({placeholders})", source_ids)

        self.db.execute(
            f"DELETE FROM source_tombstones WHERE source_id IN ({placeholders})",
            source_ids)
        self.db.execute(f"DELETE FROM sources WHERE id IN ({placeholders})", source_ids)
        self.db.commit()

        # Rebuild FTS5 to reclaim space
        self.db.execute("INSERT INTO chunks(chunks) VALUES('rebuild')")
        self.db.execute("INSERT INTO chunks_trigram(chunks_trigram) VALUES('rebuild')")
        self.db.commit()

        return len(source_ids)

    # ─── Stats ───────────────────────────────────────────────────────

    def _files_for(self, path: Path) -> int:
        """Size of one SQLite DB plus its WAL/SHM sidecars, in bytes."""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def _total_db_size(self) -> int:
        """Total on-disk footprint of the knowledge store, in bytes.

        Includes the sibling `vectors.db` when a VectorBackend is attached: the
        vector store is deliberately placed under the same storage root so the
        NFR2 budget (<600MB) is ONE checkable number — reporting only
        conscio.db would under-report it by the size of the float32 blobs
        (~330MB at target corpus scale), i.e. the check would pass while the
        real budget was blown.
        """
        total = self._files_for(Path(str(self.db_path)))
        vec_path = getattr(self.vector_backend, "db_path", None)
        if vec_path is not None:
            vec_path = Path(str(vec_path))
            # Guard against double counting if both ever point at one file.
            if vec_path != Path(str(self.db_path)):
                total += self._files_for(vec_path)
        return total

    # ─── Tombstone ─────────────────────────────────────────────────

    def _mark_stale(self, source_id: int, reason: str = "content_changed") -> None:
        """Mark a source's chunks as stale (tombstone). Does NOT delete chunks."""
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO source_tombstones (source_id, reason) VALUES (?, ?)",
                (source_id, reason),
            )
            self.db.commit()
        except Exception:
            # Source may not exist (FK violation) — ignore silently
            pass

    def list_tombstones(self) -> list[dict]:
        """Return all tombstoned sources with reason and timestamp."""
        rows = self.db.execute(
            """SELECT t.source_id, t.reason, t.created_at, s.label
               FROM source_tombstones t
               JOIN sources s ON s.id = t.source_id
               ORDER BY t.created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def _stale_source_ids(self) -> set[int]:
        """Return set of tombstoned source IDs (for filtering in search)."""
        rows = self.db.execute("SELECT source_id FROM source_tombstones").fetchall()
        return {r["source_id"] for r in rows}

    def stats(self) -> dict:
        """Return store statistics."""
        source_count = self.db.execute("SELECT COUNT(*) as c FROM sources").fetchone()["c"]
        chunk_count = self.db.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
        trigram_count = self.db.execute("SELECT COUNT(*) as c FROM chunks_trigram").fetchone()["c"]

        categories = self.db.execute(
            "SELECT source_category, COUNT(*) as c FROM sources GROUP BY source_category"
        ).fetchall()

        total_bytes = self._total_db_size()
        out = {
            "source_count": source_count,
            "chunk_count": chunk_count,
            "trigram_chunk_count": trigram_count,
            "categories": {r["source_category"]: r["c"] for r in categories},
            "db_path": str(self.db_path),
            "db_size_kb": round(total_bytes / 1024, 1),
            "db_size_mb": round(total_bytes / 1024 / 1024, 2),
        }
        if self.vector_backend is not None:
            try:
                out["vector_count"] = self.vector_backend.stats()["vectors"]
                out["vector_db_size_mb"] = round(
                    self._files_for(Path(str(self.vector_backend.db_path))) / 1024 / 1024, 2
                )
            except Exception as e:  # stats must never break a caller
                logger.debug(f"ContentStore.stats: vector stats unavailable: {e}")
        return out

    # ─── Lifecycle ───────────────────────────────────────────────────

    def close(self) -> None:
        """Close the database connection."""
        self.db.close()

    def dump(self, target_path: str | Path) -> None:
        """Atomic backup via sqlite3 backup API."""
        import sqlite3 as _sql
        dst = _sql.connect(str(target_path))
        self.db.backup(dst)
        dst.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
