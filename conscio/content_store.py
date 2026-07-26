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
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .constants import DEFAULT_DB_PATH
from .timeutil import naive_utcnow

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

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db = sqlite3.connect(str(self.db_path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.row_factory = sqlite3.Row

        self._init_schema()

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
            source_id of the created source
        """
        if category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{category}'. Must be one of: {VALID_CATEGORIES}")
        if content_type not in VALID_CONTENT_TYPES:
            raise ValueError(f"Invalid content_type '{content_type}'. Must be one of: {VALID_CONTENT_TYPES}")

        content_hash = hashlib.sha256(content.encode()).hexdigest()
        timestamp = naive_utcnow().isoformat()

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
                return source_id
            label = existing["label"]
            chunks = self._chunk_content(content, chunk_size=chunk_size, overlap=overlap, content_type=content_type)
            for i, chunk in enumerate(chunks):
                title = (f"{label}" if len(chunks) == 1
                         else f"{label} [part {i+1}/{len(chunks)}]")
                for table in ("chunks", "chunks_trigram"):
                    self.db.execute(
                        f"INSERT INTO {table} (title, content, source_id,"
                        " content_type, source_category, session_id, timestamp)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (title, chunk, source_id, content_type, category,
                         session_id, timestamp))
            self.db.commit()
            return source_id

        # Create source record
        cursor = self.db.execute(
            "INSERT INTO sources (label, source_category, content_hash) VALUES (?, ?, ?)",
            (label, category, content_hash),
        )
        source_id = int(cursor.lastrowid or 0)

        # Split into chunks at semantic boundaries (YAML, headings, or paragraphs)
        chunks = self._chunk_content(content, chunk_size=chunk_size, overlap=overlap, content_type=content_type)

        for i, chunk in enumerate(chunks):
            title = f"{label}" if len(chunks) == 1 else f"{label} [part {i+1}/{len(chunks)}]"
            # Insert into both FTS5 tables
            for table in ("chunks", "chunks_trigram"):
                self.db.execute(
                    f"INSERT INTO {table} (title, content, source_id, content_type, source_category, session_id, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (title, chunk, source_id, content_type, category, session_id, timestamp),
                )

        # Update chunk count
        self.db.execute(
            "UPDATE sources SET chunk_count = ? WHERE id = ?",
            (len(chunks), source_id),
        )
        self.db.commit()

        return source_id

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

        Returns:
            List of SearchResult sorted by RRF score (descending)
        """
        if not query.strip():
            return []

        # Build WHERE clause for filters
        filter_clause = ""
        filter_params: list = []

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

        # Trigram search (BM25)
        trigram_results = self._fts_search(
            "chunks_trigram", query, limit * 3, filter_clause, filter_params
        )

        # Merge via RRF
        merged = self._rrf_merge(porter_results, trigram_results)

        return merged[:limit]

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

        self.db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        self.db.commit()
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

        # Batch delete in single transaction: 3 DELETEs + 1 commit
        for table in ("chunks", "chunks_trigram"):
            self.db.execute(f"DELETE FROM {table} WHERE source_id IN ({placeholders})", source_ids)

        self.db.execute(f"DELETE FROM sources WHERE id IN ({placeholders})", source_ids)
        self.db.commit()

        # Rebuild FTS5 to reclaim space
        self.db.execute("INSERT INTO chunks(chunks) VALUES('rebuild')")
        self.db.execute("INSERT INTO chunks_trigram(chunks_trigram) VALUES('rebuild')")
        self.db.commit()

        return len(source_ids)

    # ─── Stats ───────────────────────────────────────────────────────

    def _total_db_size(self) -> int:
        """Total size of DB + WAL + SHM files in bytes."""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.db_path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    def stats(self) -> dict:
        """Return store statistics."""
        source_count = self.db.execute("SELECT COUNT(*) as c FROM sources").fetchone()["c"]
        chunk_count = self.db.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
        trigram_count = self.db.execute("SELECT COUNT(*) as c FROM chunks_trigram").fetchone()["c"]

        categories = self.db.execute(
            "SELECT source_category, COUNT(*) as c FROM sources GROUP BY source_category"
        ).fetchall()

        return {
            "source_count": source_count,
            "chunk_count": chunk_count,
            "trigram_chunk_count": trigram_count,
            "categories": {r["source_category"]: r["c"] for r in categories},
            "db_path": str(self.db_path),
            "db_size_kb": round(self._total_db_size() / 1024, 1),
            "db_size_mb": round(self._total_db_size() / 1024 / 1024, 2),
        }

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
