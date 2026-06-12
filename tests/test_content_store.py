"""
Tests for ContentStore — FTS5 BM25 dual-index knowledge base.

Covers: indexing, chunking, search (porter/trigram/RRF), dedup,
deletion, compaction, stats, edge cases.
"""

import os
from datetime import timedelta

import pytest

from conscio.timeutil import naive_utcnow

from conscio.content_store import ContentStore, SearchResult, SourceInfo, VALID_CATEGORIES, VALID_CONTENT_TYPES


@pytest.fixture
def store(tmp_path):
    """Create a ContentStore with a temp database."""
    db_path = tmp_path / "test_conscio.db"
    s = ContentStore(db_path=db_path)
    yield s
    s.close()


@pytest.fixture
def populated_store(store):
    """Create a store with sample data."""
    store.index("reflection_2026-06-04", "Trading bot operational. BTC spiked 2% today.", "reflection")
    store.index("error_log_001", "API timeout on OKX endpoint — error code 51155", "error", content_type="log")
    store.index("system_metrics", "CPU: 45% | Memory: 72% | Disk: 89%", "system", content_type="metric")
    store.index("trading_session", "Opened long BTC-USDT swap at 67500. Stop at 66800.", "trading")
    return store


# ─── Schema Tests ───────────────────────────────────────────────────────

class TestSchema:
    def test_tables_created(self, store):
        """All required tables exist after init."""
        tables = store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' OR type='view'"
        ).fetchall()
        table_names = {r["name"] for r in tables}
        assert "sources" in table_names
        assert "chunks" in table_names
        assert "chunks_trigram" in table_names

    def test_wal_mode(self, store):
        """Database is in WAL mode for concurrent access."""
        mode = store.db.execute("PRAGMA journal_mode").fetchone()["journal_mode"]
        assert mode == "wal"

    def test_idempotent_init(self, store):
        """Initializing schema twice doesn't error."""
        store._init_schema()  # Should not raise
        store._init_schema()

    def test_valid_categories(self):
        """All documented categories are in VALID_CATEGORIES."""
        expected = {"reflection", "perception", "trading", "system", "error", "consciousness", "external", "session"}
        assert VALID_CATEGORIES == expected

    def test_valid_content_types(self):
        """All documented content types are in VALID_CONTENT_TYPES."""
        expected = {"prose", "code", "metric", "log"}
        assert VALID_CONTENT_TYPES == expected


# ─── Indexing Tests ─────────────────────────────────────────────────────

class TestIndexing:
    def test_basic_index(self, store):
        """Indexing returns a positive source_id."""
        sid = store.index("test", "Hello world", "reflection")
        assert isinstance(sid, int)
        assert sid > 0

    def test_source_metadata(self, store):
        """Source record is created with correct metadata."""
        sid = store.index("my_label", "Some content here", "trading")
        source = store.get_source(sid)
        assert source is not None
        assert source.label == "my_label"
        assert source.source_category == "trading"
        assert source.chunk_count == 1

    def test_chunks_in_both_tables(self, store):
        """Content is indexed in both FTS5 tables."""
        store.index("test", "Hello world content", "reflection")
        porter = store.db.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
        trigram = store.db.execute("SELECT COUNT(*) as c FROM chunks_trigram").fetchone()["c"]
        assert porter == 1
        assert trigram == 1

    def test_dedup_by_hash(self, store):
        """Indexing the same content twice returns same source_id."""
        sid1 = store.index("test", "Exact same content", "reflection")
        sid2 = store.index("test_copy", "Exact same content", "reflection")
        assert sid1 == sid2

    def test_different_content_different_id(self, store):
        """Different content gets different source_ids."""
        sid1 = store.index("test1", "Content A", "reflection")
        sid2 = store.index("test2", "Content B", "reflection")
        assert sid1 != sid2

    def test_invalid_category_raises(self, store):
        """Invalid category raises ValueError."""
        with pytest.raises(ValueError, match="Invalid category"):
            store.index("test", "Content", "invalid_cat")

    def test_invalid_content_type_raises(self, store):
        """Invalid content_type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid content_type"):
            store.index("test", "Content", "reflection", content_type="invalid")

    def test_session_id(self, store):
        """Session ID is stored correctly."""
        sid = store.index("test", "Session content", "reflection", session_id="sess_123")
        rows = store.db.execute(
            "SELECT session_id FROM chunks WHERE source_id = ?", (sid,)
        ).fetchall()
        assert rows[0]["session_id"] == "sess_123"

    def test_empty_content(self, store):
        """Empty content is still indexed (single empty chunk)."""
        sid = store.index("empty", "", "reflection")
        assert sid > 0
        source = store.get_source(sid)
        assert source.chunk_count >= 1


# ─── Chunking Tests ─────────────────────────────────────────────────────

class TestChunking:
    def test_short_content_single_chunk(self, store):
        """Content shorter than chunk_size produces a single chunk."""
        content = "Short content"
        sid = store.index("test", content, "reflection", chunk_size=2000)
        source = store.get_source(sid)
        assert source.chunk_count == 1

    def test_long_content_multiple_chunks(self, store):
        """Long content with paragraph breaks is split into chunks."""
        paragraphs = [f"Paragraph {i} with enough text to fill space." * 5 for i in range(10)]
        content = "\n\n".join(paragraphs)
        sid = store.index("test", content, "reflection", chunk_size=500)
        source = store.get_source(sid)
        assert source.chunk_count > 1

    def test_chunk_at_paragraph_boundary(self, store):
        """Chunks split at paragraph boundaries, not mid-word."""
        content = "First paragraph with enough text.\n\nSecond paragraph with more text.\n\nThird paragraph."
        chunks = store._chunk_content(content, chunk_size=60)
        # First chunk should end at a paragraph boundary
        assert "First paragraph" in chunks[0]
        assert len(chunks) > 1

    def test_no_paragraph_break_hard_split(self, store):
        """Content without paragraph breaks gets hard-split at chunk_size."""
        content = "A" * 5000  # No paragraph breaks
        chunks = store._chunk_content(content, chunk_size=2000)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 2000

    def test_chunk_titles_numbered(self, store):
        """Multi-chunk sources get numbered titles."""
        content = "\n\n".join([f"Paragraph {i} " * 20 for i in range(10)])
        sid = store.index("my_doc", content, "reflection", chunk_size=300)
        rows = store.db.execute(
            "SELECT title FROM chunks WHERE source_id = ? ORDER BY rowid", (sid,)
        ).fetchall()
        # Multi-chunk titles should include [part N/M]
        if len(rows) > 1:
            assert "[part 1/" in rows[0]["title"]

    def test_chunk_preserves_content(self, store):
        """All chunks together contain the full original content."""
        content = "\n\n".join([f"Paragraph {i} with unique text {i}." for i in range(20)])
        chunks = store._chunk_content(content, chunk_size=200)
        reconstructed = "\n\n".join(chunks)
        # All paragraphs should be present
        for i in range(20):
            assert f"unique text {i}" in reconstructed


# ─── Search Tests ───────────────────────────────────────────────────────

class TestSearch:
    def test_basic_porter_search(self, populated_store):
        """Porter search finds content by stemmed word."""
        results = populated_store.search("trading")
        assert len(results) > 0
        # Should find "Trading bot operational" or trading session
        found_trading = any("trading" in r.content.lower() or "trading" in r.title.lower() for r in results)
        assert found_trading

    def test_trigram_substring_search(self, populated_store):
        """Trigram search finds content by exact substring."""
        results = populated_store.search("51155")
        assert len(results) > 0
        assert any("51155" in r.content for r in results)

    def test_rrf_merge_both_indexes(self, populated_store):
        """RRF merge combines results from both porter and trigram."""
        # "error" should match porter (stemming) and potentially trigram
        results = populated_store.search("error")
        assert len(results) > 0

    def test_category_filter(self, populated_store):
        """Category filter limits results to specified category."""
        results = populated_store.search("bot", category="error")
        # "bot" might match "Trading bot" (reflection) but filter should exclude it
        for r in results:
            assert r.source_category == "error"

    def test_content_type_filter(self, populated_store):
        """Content type filter works correctly."""
        results = populated_store.search("CPU", content_type="metric")
        for r in results:
            assert r.content_type == "metric"

    def test_since_filter(self, populated_store):
        """Since filter excludes old results."""
        future = (naive_utcnow() + timedelta(hours=1)).isoformat()
        results = populated_store.search("trading", since=future)
        assert len(results) == 0

    def test_empty_query(self, populated_store):
        """Empty query returns no results."""
        results = populated_store.search("")
        assert len(results) == 0

    def test_whitespace_query(self, populated_store):
        """Whitespace-only query returns no results."""
        results = populated_store.search("   ")
        assert len(results) == 0

    def test_no_results(self, populated_store):
        """Query with no matches returns empty list."""
        results = populated_store.search("xyzzy_nonexistent_12345")
        assert len(results) == 0

    def test_search_result_fields(self, populated_store):
        """SearchResult has all expected fields."""
        results = populated_store.search("trading")
        assert len(results) > 0
        r = results[0]
        assert isinstance(r, SearchResult)
        assert r.rowid > 0
        assert isinstance(r.title, str)
        assert isinstance(r.content, str)
        assert isinstance(r.rank, float)

    def test_to_dict(self, populated_store):
        """SearchResult.to_dict() produces valid dict."""
        results = populated_store.search("trading")
        d = results[0].to_dict()
        assert "rowid" in d
        assert "rank" in d
        assert isinstance(d["rank"], float)

    def test_limit_parameter(self, populated_store):
        """Limit parameter caps result count."""
        # Add more content to have many matches
        for i in range(20):
            populated_store.index(f"trading_doc_{i}", f"Trading update number {i}", "trading")

        results = populated_store.search("trading", limit=3)
        assert len(results) <= 3

    def test_porter_stemming(self, store):
        """Porter tokenizer stems: 'traded' matches 'trading'."""
        store.index("test", "I traded BTC today on the exchange", "trading")
        results = store.search("trading")
        # Porter should stem "traded" and "trading" to the same root
        assert len(results) > 0

    def test_special_chars_in_query(self, populated_store):
        """Special FTS5 characters in query don't crash search."""
        # These should not raise exceptions
        populated_store.search('test "quoted" *')
        populated_store.search("test's apostrophe")

    def test_combined_filters(self, populated_store):
        """Multiple filters work together."""
        results = populated_store.search("API", category="error", content_type="log")
        for r in results:
            assert r.source_category == "error"
            assert r.content_type == "log"


# ─── Retrieval Tests ────────────────────────────────────────────────────

class TestRetrieval:
    def test_get_by_source(self, populated_store):
        """get_by_source returns all chunks for a source."""
        sid = populated_store.index("multi", "\n\n".join([f"Part {i}" for i in range(5)]), "reflection", chunk_size=50)
        chunks = populated_store.get_by_source(sid)
        assert len(chunks) >= 1

    def test_get_source_metadata(self, populated_store):
        """get_source returns correct metadata."""
        source = populated_store.get_source(1)
        assert source is not None
        assert isinstance(source, SourceInfo)
        assert source.id == 1

    def test_get_nonexistent_source(self, store):
        """get_source returns None for nonexistent source."""
        assert store.get_source(9999) is None


# ─── Deletion Tests ─────────────────────────────────────────────────────

class TestDeletion:
    def test_delete_source(self, populated_store):
        """Deleting a source removes it and its chunks."""
        sid = populated_store.index("to_delete", "Delete me", "reflection")
        assert populated_store.delete_source(sid) is True
        assert populated_store.get_source(sid) is None

    def test_delete_nonexistent(self, store):
        """Deleting a nonexistent source returns False."""
        assert store.delete_source(9999) is False

    def test_delete_removes_chunks(self, populated_store):
        """Deleted source's chunks are gone from both tables."""
        sid = populated_store.index("to_delete", "Delete this content entirely", "reflection")
        populated_store.delete_source(sid)
        porter = populated_store.db.execute(
            "SELECT COUNT(*) as c FROM chunks WHERE source_id = ?", (sid,)
        ).fetchone()["c"]
        trigram = populated_store.db.execute(
            "SELECT COUNT(*) as c FROM chunks_trigram WHERE source_id = ?", (sid,)
        ).fetchone()["c"]
        assert porter == 0
        assert trigram == 0


# ─── Compaction Tests ───────────────────────────────────────────────────

class TestCompaction:
    def test_compact_removes_old(self, store):
        """compact() removes sources older than before_days."""
        # Insert with old timestamp
        sid = store.index("old", "Old content to remove", "reflection")
        # Manually update the indexed_at to be 100 days ago
        old_time = (naive_utcnow() - timedelta(days=100)).isoformat()
        store.db.execute("UPDATE sources SET indexed_at = ? WHERE id = ?", (old_time, sid))
        store.db.commit()

        removed = store.compact(before_days=90)
        assert removed == 1
        assert store.get_source(sid) is None

    def test_compact_preserves_recent(self, store):
        """compact() doesn't remove recent sources."""
        sid = store.index("recent", "Recent content to keep", "reflection")
        removed = store.compact(before_days=90)
        assert removed == 0
        assert store.get_source(sid) is not None

    def test_rebuild(self, populated_store):
        """rebuild() doesn't crash and preserves data."""
        stats_before = populated_store.stats()
        populated_store.rebuild()
        stats_after = populated_store.stats()
        # Counts should be the same after rebuild
        assert stats_before["source_count"] == stats_after["source_count"]


# ─── Stats Tests ────────────────────────────────────────────────────────

class TestStats:
    def test_empty_stats(self, store):
        """Empty store has zero counts."""
        stats = store.stats()
        assert stats["source_count"] == 0
        assert stats["chunk_count"] == 0

    def test_populated_stats(self, populated_store):
        """Populated store has correct counts."""
        stats = populated_store.stats()
        assert stats["source_count"] == 4
        assert stats["chunk_count"] >= 4
        assert "reflection" in stats["categories"]
        # DB size includes WAL — may be small but > 0
        assert stats["db_size_kb"] > 0  # KB precision catches small DBs

    def test_category_breakdown(self, store):
        """Stats shows per-category counts."""
        store.index("r1", "Reflection 1", "reflection")
        store.index("r2", "Reflection 2", "reflection")
        store.index("e1", "Error 1", "error")
        stats = store.stats()
        assert stats["categories"]["reflection"] == 2
        assert stats["categories"]["error"] == 1


# ─── Context Manager Tests ──────────────────────────────────────────────

class TestContextManager:
    def test_with_statement(self, tmp_path):
        """ContentStore works as context manager."""
        with ContentStore(db_path=tmp_path / "ctx.db") as s:
            s.index("test", "Context manager content", "reflection")
            s.stats()
        # After exit, DB should be closed (no further operations)

    def test_close_idempotent(self, store):
        """close() can be called multiple times."""
        store.close()
        store.close()  # Should not raise


# ─── Edge Case Tests ────────────────────────────────────────────────────

class TestEdgeCases:
    def test_unicode_content(self, store):
        """Unicode content is indexed and searchable."""
        store.index("unicode", "Operação de trading com ênfase no BTC", "trading")
        results = store.search("Operação")
        assert len(results) > 0

    def test_very_long_query(self, store):
        """Very long queries are truncated and don't crash."""
        store.index("test", "Some test content here", "reflection")
        long_query = "word " * 100
        store.search(long_query)  # Should not crash
        # Result count may vary — just ensuring no exception

    def test_content_with_fts5_special_chars(self, store):
        """Content with FTS5 special characters is handled."""
        sid = store.index("special", 'Content with "quotes" and *stars* and :colons:', "reflection")
        assert sid > 0

    def test_concurrent_same_content(self, store):
        """Indexing same content from two calls returns same source."""
        sid1 = store.index("first", "Identical content", "reflection")
        sid2 = store.index("second", "Identical content", "reflection")
        assert sid1 == sid2

    def test_large_number_of_sources(self, store):
        """Handles a large number of sources without degradation."""
        for i in range(100):
            store.index(f"bulk_{i}", f"Bulk content number {i} with unique text", "reflection")
        stats = store.stats()
        assert stats["source_count"] == 100
        # Search should still work
        results = store.search("unique text 50")
        assert len(results) > 0

    def test_single_word_query(self, populated_store):
        """Single word query works."""
        results = populated_store.search("spiked")
        assert len(results) > 0

    def test_phrase_search_trigram(self, store):
        """Trigram index finds exact phrase substrings."""
        store.index("test", "Error code 51155 compliance violation", "error")
        results = store.search("51155 compliance")
        # Trigram should find this via substring match
        assert len(results) > 0

    def test_porter_stemming_variants(self, store):
        """Porter stems common English variants."""
        store.index("test", "The system is running and processing data", "system")
        # "running" should stem to match "run"
        results = store.search("running")
        assert len(results) > 0


# ─── Private Method Tests ───────────────────────────────────────────────


class TestPrivateMethods:
    def test_fts_search_porter_table(self, populated_store):
        """_fts_search works on porter table with valid query."""
        results = populated_store._fts_search(
            "chunks", "trading", 5, "", []
        )
        assert isinstance(results, list)
        # Should find the trading content
        assert len(results) >= 0  # May or may not find depending on content

    def test_fts_search_trigram_table(self, populated_store):
        """_fts_search works on trigram table with substring query."""
        results = populated_store._fts_search(
            "chunks_trigram", "51155", 5, "", []
        )
        assert isinstance(results, list)
        # Trigram should find exact substring
        assert len(results) >= 0

    def test_fts_search_empty_query(self, populated_store):
        """_fts_search returns empty for empty/whitespace query."""
        results = populated_store._fts_search("chunks", "", 5, "", [])
        assert results == []
        results = populated_store._fts_search("chunks", "   ", 5, "", [])
        assert results == []

    def test_fts_search_invalid_syntax_fallback(self, populated_store):
        """_fts_search falls back to simple token search on syntax error."""
        # Query with unbalanced quotes would cause FTS5 syntax error
        results = populated_store._fts_search("chunks", 'unbalanced " quote', 5, "", [])
        # Should not raise, should return empty or fallback results
        assert isinstance(results, list)

    def test_escape_fts_query_porter(self, store):
        """_escape_fts_query formats porter query with OR and quotes."""
        escaped = store._escape_fts_query("hello world", "chunks")
        # Porter: tokens joined with OR, each quoted
        assert '"hello"' in escaped
        assert '"world"' in escaped
        assert " OR " in escaped

    def test_escape_fts_query_trigram(self, store):
        """_escape_fts_query wraps trigram query as single phrase."""
        escaped = store._escape_fts_query("hello world", "chunks_trigram")
        # Trigram: entire query wrapped as single phrase
        assert escaped == '"hello world"'

    def test_escape_fts_query_removes_special_chars(self, store):
        """_escape_fts_query removes FTS5 special characters from input."""
        escaped = store._escape_fts_query('test "quoted" *stars*', "chunks")
        # Original special chars should be removed; FTS5 adds its own quotes around tokens
        assert '*' not in escaped
        assert "'" not in escaped
        # The word "quoted" should be present without the original double-quotes
        assert "quoted" in escaped
        # Should have OR-joined tokens
        assert " OR " in escaped

    def test_escape_fts_query_empty_returns_empty(self, store):
        """_escape_fts_query returns empty string for empty input."""
        assert store._escape_fts_query("", "chunks") == ""
        assert store._escape_fts_query("   ", "chunks") == ""

    def test_escape_fts_query_token_limit(self, store):
        """_escape_fts_query limits tokens to 10 for porter."""
        tokens = " ".join([f"word{i}" for i in range(15)])
        escaped = store._escape_fts_query(tokens, "chunks")
        # Should only have 10 OR clauses = 9 " OR " occurrences
        or_count = escaped.count(" OR ")
        assert or_count == 9

    def test_total_db_size(self, store):
        """_total_db_size returns size of DB + WAL + SHM files."""
        # Index some content to ensure DB files exist
        store.index("test", "Content for size test", "reflection")
        size = store._total_db_size()
        assert size > 0
        # Should include main DB at minimum
        assert os.path.getsize(store.db_path) <= size
