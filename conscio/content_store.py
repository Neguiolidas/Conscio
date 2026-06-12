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
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
    content_hash: Optional[str] = None


# ─── Constants ──────────────────────────────────────────────────────────

DEFAULT_DB_PATH = Path.home() / ".hermes" / "consciousness" / "conscio.db"

VALID_CATEGORIES = {"reflection", "perception", "trading", "system", "error", "consciousness", "external", "session"}
VALID_CONTENT_TYPES = {"prose", "code", "metric", "log"}

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
    ) -> int:
        """
        Index content into FTS5 (porter + trigram).

        Long content is split into chunks of ~chunk_size characters
        at paragraph boundaries for better search granularity.

        Args:
            label: Human-readable source label (e.g., "reflection_2026-06-04")
            content: Text content to index
            category: One of VALID_CATEGORIES
            content_type: One of VALID_CONTENT_TYPES
            session_id: Optional session identifier
            chunk_size: Max chars per chunk (split at paragraph boundaries)

        Returns:
            source_id of the created source
        """
        if category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid category '{category}'. Must be one of: {VALID_CATEGORIES}")
        if content_type not in VALID_CONTENT_TYPES:
            raise ValueError(f"Invalid content_type '{content_type}'. Must be one of: {VALID_CONTENT_TYPES}")

        content_hash = hashlib.sha256(content.encode()).hexdigest()
        timestamp = naive_utcnow().isoformat()

        # Check for duplicate content (same hash = already indexed)
        existing = self.db.execute(
            "SELECT id FROM sources WHERE content_hash = ? LIMIT 1",
            (content_hash,),
        ).fetchone()
        if existing:
            return existing["id"]

        # Create source record
        cursor = self.db.execute(
            "INSERT INTO sources (label, source_category, content_hash) VALUES (?, ?, ?)",
            (label, category, content_hash),
        )
        source_id = int(cursor.lastrowid or 0)

        # Split into chunks at paragraph boundaries
        chunks = self._chunk_content(content, chunk_size)

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

    def _chunk_content(self, content: str, chunk_size: int = 2000) -> list[str]:
        """
        Split content into chunks at paragraph boundaries.

        Each chunk is at most chunk_size characters, split at the last
        paragraph break (\\n\\n) before the limit. This preserves
        semantic coherence within chunks.
        """
        if len(content) <= chunk_size:
            return [content]

        chunks = []
        remaining = content

        while remaining:
            if len(remaining) <= chunk_size:
                chunks.append(remaining)
                break

            # Find last paragraph break within chunk_size
            split_at = remaining[:chunk_size].rfind("\n\n")

            if split_at == -1:
                # No paragraph break found — hard split at chunk_size
                split_at = chunk_size
            else:
                split_at += 2  # Include the \n\n

            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()

            if not remaining:
                break

        return [c for c in chunks if c]  # Remove empty chunks

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

    def get_source(self, source_id: int) -> Optional[SourceInfo]:
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

    def rebuild(self) -> None:
        """Rebuild FTS5 indexes (reclaim space after deletions)."""
        self.db.execute("INSERT INTO chunks(chunks) VALUES('rebuild')")
        self.db.execute("INSERT INTO chunks_trigram(chunks_trigram) VALUES('rebuild')")
        self.db.commit()

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

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
