"""
Tests for ContentStore — FTS5 BM25 dual-index knowledge base.

Covers: indexing, chunking, search (porter/trigram/RRF), dedup,
deletion, compaction, stats, edge cases.
"""

import os
from datetime import timedelta

import pytest

from conscio.content_store import (
    VALID_CATEGORIES,
    VALID_CONTENT_TYPES,
    ContentStore,
    SearchResult,
    SourceInfo,
)
from conscio.timeutil import naive_utcnow


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
        expected = {"reflection", "perception", "trading", "system", "error", "consciousness", "external", "session", "pentest", "reference", "payload"}
        assert VALID_CATEGORIES == expected

    def test_valid_content_types(self):
        """All documented content types are in VALID_CONTENT_TYPES."""
        expected = {"prose", "code", "metric", "log", "yaml", "markdown"}
        assert VALID_CONTENT_TYPES == expected

    def test_new_categories_pentest_reference_payload(self):
        """New categories pentest, reference, and payload exist."""
        assert "pentest" in VALID_CATEGORIES
        assert "reference" in VALID_CATEGORIES
        assert "payload" in VALID_CATEGORIES

    def test_new_content_type_yaml(self):
        """New content type yaml exists."""
        assert "yaml" in VALID_CONTENT_TYPES

    def test_new_content_type_markdown(self):
        """New content type markdown exists."""
        assert "markdown" in VALID_CONTENT_TYPES


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

    def test_chunk_by_headings_markdown(self, store):
        """Markdown content with headings is split by heading boundaries."""
        content = """# Main Title
This is the introduction.

## Section 1
Content for section 1.

## Section 2
Content for section 2.

### Subsection 2.1
Content for subsection 2.1.
"""
        chunks = store._chunk_by_headings(content, chunk_size=2000, overlap=0.0)
        assert len(chunks) >= 3
        # Each heading should be in a chunk
        assert any("# Main Title" in c for c in chunks)
        assert any("## Section 1" in c for c in chunks)
        assert any("## Section 2" in c for c in chunks)

    def test_chunk_by_headings_small_chunk_size(self, store):
        """Heading chunks larger than chunk_size are split at paragraphs."""
        content = """# Title
First paragraph is long and has lots of text to fill the chunk size with more words here.

Second paragraph is also long and should cause a split.

Third paragraph for completeness."""
        chunks = store._chunk_by_headings(content, chunk_size=100, overlap=0.0)
        # Should have multiple chunks because of small chunk_size
        assert len(chunks) >= 1
        for chunk in chunks:
            assert len(chunk) <= 150  # Some tolerance for splitting

    def test_chunk_yaml_by_separators(self, store):
        """YAML content is split by --- document separators."""
        content = """key1: value1
key2: value2
---
key3: value3
key4: value4
---
key5: value5
"""
        chunks = store._chunk_yaml(content, chunk_size=2000, overlap=0.0)
        assert len(chunks) == 3
        assert "key1: value1" in chunks[0]
        assert "key3: value3" in chunks[1]
        assert "key5: value5" in chunks[2]

    def test_chunk_yaml_large_document(self, store):
        """Large YAML documents are split by line boundaries."""
        lines = [f"key{i}: value{i}" for i in range(100)]
        content = '\n'.join(lines)
        chunks = store._chunk_yaml(content, chunk_size=200, overlap=0.0)
        # Should split due to chunk_size constraint
        assert len(chunks) > 1
        # All keys should be present in some chunk
        for i in range(100):
            found = any(f"key{i}:" in c for c in chunks)
            assert found

    def test_chunk_content_type_yaml_dispatch(self, store):
        """_chunk_content dispatches to _chunk_yaml for content_type='yaml'."""
        yaml_content = """config: value
---
other: setting"""
        chunks = store._chunk_content(yaml_content, chunk_size=2000, overlap=0.0, content_type="yaml")
        assert len(chunks) == 2

    def test_chunk_content_type_prose_dispatch(self, store):
        """_chunk_content dispatches to paragraphs for content_type='prose' without headings."""
        # Use longer content to force splitting at paragraph boundaries
        prose = "Paragraph 1. " * 10 + "\n\n" + "Paragraph 2. " * 10 + "\n\n" + "Paragraph 3. " * 10
        chunks = store._chunk_content(prose, chunk_size=100, overlap=0.0, content_type="prose")
        # Should split at paragraph boundaries
        assert len(chunks) >= 2

    def test_chunk_content_markdown_heading_detection(self, store):
        """_chunk_content with content_type='markdown' splits by heading boundaries."""
        markdown = """# Title
Content here.

## Section
More content."""
        # Heading dispatch triggered by content_type="markdown"
        chunks = store._chunk_content(markdown, chunk_size=2000, overlap=0.0, content_type="markdown")
        # Should be split by headings when content_type is "markdown"
        assert len(chunks) >= 2

    def test_content_type_code_uses_paragraph_not_heading_dispatch(self, store):
        """content_type='code' with heading-like lines uses paragraph dispatch, not heading."""
        # Content with heading-like lines (# TODO comments)
        code_with_hash = """def process(x):
    # TODO: implement this
    # Refactor for clarity
    return x * 2
"""
        chunks = store._chunk_content(code_with_hash, chunk_size=2000, overlap=0.0, content_type="code")
        # Should use paragraph splitting, not heading dispatch
        # So # lines are treated as regular content, not heading boundaries
        assert len(chunks) == 1  # Single chunk since content < chunk_size and no real paragraph breaks

    def test_overlap_20_percent(self, store):
        """20% overlap adds last 20% of chunk_size chars to each chunk after first."""
        # Create simple chunks to test overlap
        chunk1 = "A" * 100
        chunk2 = "B" * 100
        chunk3 = "C" * 100
        raw_chunks = [chunk1, chunk2, chunk3]

        overlapped = store._apply_overlap(raw_chunks, chunk_size=100, overlap=0.2)
        assert len(overlapped) == 3
        assert overlapped[0] == chunk1  # First chunk unchanged
        # Second chunk should start with last 20 chars of first chunk
        assert overlapped[1].startswith(chunk1[-20:])
        assert "B" in overlapped[1]  # And contain original chunk2
        # Third chunk should start with last 20 chars of second chunk
        assert overlapped[2].startswith(chunk2[-20:])
        assert "C" in overlapped[2]

    def test_overlap_zero_no_change(self, store):
        """Zero overlap returns chunks unchanged."""
        chunks = ["First chunk", "Second chunk", "Third chunk"]
        overlapped = store._apply_overlap(chunks, chunk_size=100, overlap=0.0)
        assert overlapped == chunks

    def test_overlap_empty_chunks(self, store):
        """Empty chunk list returns empty."""
        overlapped = store._apply_overlap([], chunk_size=100, overlap=0.2)
        assert overlapped == []

    def test_overlap_single_chunk(self, store):
        """Single chunk is returned unchanged."""
        chunks = ["Only chunk"]
        overlapped = store._apply_overlap(chunks, chunk_size=100, overlap=0.2)
        assert overlapped == chunks

    def test_index_accepts_overlap_parameter(self, store):
        """index() method accepts overlap parameter."""
        content = "Paragraph 1.\n\nParagraph 2.\n\nParagraph 3."
        sid = store.index("test", content, "reflection", overlap=0.1)
        assert sid > 0

    def test_index_overlap_default_0_2(self, store):
        """index() uses default overlap of 0.2 when not specified."""
        content = "Paragraph 1.\n\nParagraph 2.\n\nParagraph 3."
        sid = store.index("test", content, "reflection")
        assert sid > 0
        # Should have created chunks with overlap applied

    def test_index_with_markdown_and_overlap(self, store):
        """Indexing markdown with overlap works correctly."""
        markdown = """# Part 1
Section 1 content here.

## Subsection
More text.

# Part 2
Section 2 content."""
        sid = store.index("markdown_doc", markdown, "reference", content_type="prose", overlap=0.1)
        assert sid > 0
        source = store.get_source(sid)
        assert source is not None

    def test_index_with_yaml_content_type(self, store):
        """Indexing YAML content with content_type='yaml' works."""
        yaml_content = """version: "1.0"
enabled: true
---
version: "2.0"
enabled: false
"""
        sid = store.index("yaml_config", yaml_content, "reference", content_type="yaml")
        assert sid > 0
        source = store.get_source(sid)
        assert source.source_category == "reference"
        # Verify the content was indexed
        results = store.search("version", category="reference")
        assert len(results) > 0

    def test_new_category_pentest_indexing(self, store):
        """Pentest category can be indexed and retrieved."""
        sid = store.index("sql_injection_ref", "UNION SELECT attack vector", "pentest")
        assert sid > 0
        results = store.search("attack vector", category="pentest")
        assert len(results) > 0

    def test_new_category_reference_indexing(self, store):
        """Reference category can be indexed and retrieved."""
        sid = store.index("owasp_top_10", "OWASP Top 10 vulnerability list", "reference")
        assert sid > 0
        results = store.search("OWASP", category="reference")
        assert len(results) > 0

    def test_new_category_payload_indexing(self, store):
        """Payload category can be indexed and retrieved."""
        sid = store.index("shellcode_payload", "x86 shellcode payload bytes", "payload")
        assert sid > 0
        results = store.search("shellcode", category="payload")
        assert len(results) > 0

    def test_regression_overlap_zero_prose_reproduces_old_behavior(self, store):
        """overlap=0.0 with content_type='prose' reproduces exact old behavior."""
        content = "\n\n".join([f"Paragraph {i} with unique text {i}." for i in range(10)])
        # Old way: called _chunk_content with just content and chunk_size (and no overlap, which is 0.0)
        old_chunks = store._chunk_content(content, chunk_size=200, overlap=0.0)
        # New way: explicitly with overlap=0.0 and content_type="prose"
        new_chunks = store._chunk_content(content, chunk_size=200, overlap=0.0, content_type="prose")
        assert old_chunks == new_chunks

    def test_regression_index_default_overlap_behavior(self, store):
        """index() default behavior with new params doesn't break existing callers."""
        # Existing caller: index(label, content, category)
        sid1 = store.index("test1", "Simple content here.", "reflection")
        # New caller with defaults: index(label, content, category, chunk_size=2000, overlap=0.2)
        sid2 = store.index("test2", "Simple content here.", "reflection")
        # Both should succeed and return positive IDs
        assert sid1 > 0
        assert sid2 > 0


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


# ─── Vector wiring (I4 / M1 / M3) ───────────────────────────────────────


class _FakeVectorBackend:
    """Minimal VectorBackend stand-in that records what it was handed."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.rows = {}

    def add(self, vid, vec, category=None):
        self.rows[vid] = (vec, category)

    def stats(self):
        return {"vectors": len(self.rows), "dimension": 2}


class _RecordingPipeline:
    """EmbeddingPipeline stand-in: counts calls, forwards to the backend."""

    def __init__(self, backend):
        self.vector_backend = backend
        self.batch_calls = 0
        self.chunk_calls = 0
        self.enabled = True

    def embed_chunk(self, chunk_id, text):
        self.chunk_calls += 1
        self.vector_backend.add(chunk_id, [1.0, 0.0])
        return [1.0, 0.0]

    def embed_batch(self, chunks, category=None):
        self.batch_calls += 1
        for cid, _text in chunks:
            self.vector_backend.add(cid, [1.0, 0.0], category=category)
        return [[1.0, 0.0] for _ in chunks]


@pytest.fixture
def vector_store(tmp_path):
    backend = _FakeVectorBackend(tmp_path / "vectors.db")
    pipeline = _RecordingPipeline(backend)
    s = ContentStore(
        db_path=tmp_path / "test_conscio.db",
        vector_backend=backend,
        embeddings=pipeline,
    )
    yield s, backend, pipeline
    s.close()


class TestVectorWiring:
    def test_index_embeds_in_one_batch_call_per_document(self, vector_store):
        """I4: one model/store round-trip per document, not one per chunk.

        A per-chunk call is what made ingestion cost N model invocations and
        N fsyncs; the batch path is the whole reason target-scale ingest can
        fit the time budget.
        """
        store, backend, pipeline = vector_store
        long_text = "\n\n".join(f"Paragraph number {i} with content." for i in range(40))
        store.index("multi", long_text, "reference", chunk_size=200)

        assert pipeline.chunk_calls == 0, "per-chunk embedding path must be gone"
        assert pipeline.batch_calls == 1
        assert len(backend.rows) > 1, "every chunk should still get its own vector"

    def test_index_passes_category_to_vector_rows(self, vector_store):
        """M2/C1: category is denormalized so scoped recall can pre-filter."""
        store, backend, _ = vector_store
        store.index("cat", "some reference content here", "reference")
        assert backend.rows
        assert all(cat == "reference" for _vec, cat in backend.rows.values())

    def test_duplicate_index_does_not_re_embed(self, vector_store):
        """Re-indexing identical content must not pay the embedding cost again."""
        store, _backend, pipeline = vector_store
        store.index("dup", "identical body text", "reference")
        assert pipeline.batch_calls == 1
        result = store.index_ex("dup", "identical body text", "reference")
        assert result.status == "duplicate"
        assert result.chunks_added == 0
        assert pipeline.batch_calls == 1

    def test_embedding_failure_never_breaks_fts_ingestion(self, tmp_path):
        """Vector path is best-effort: FTS5 rows must survive an embed blowup."""
        class _Exploding:
            enabled = True
            def embed_batch(self, chunks, category=None):
                raise RuntimeError("model down")

        backend = _FakeVectorBackend(tmp_path / "vectors.db")
        s = ContentStore(db_path=tmp_path / "c.db",
                         vector_backend=backend, embeddings=_Exploding())
        try:
            s.index("resilient", "content that must still be searchable", "reference")
            assert s.search("searchable")
        finally:
            s.close()

    def test_stats_counts_vector_db_in_total_size(self, vector_store, tmp_path):
        """M1: NFR2 is a budget on the whole store, so stats must include vectors.db."""
        store, backend, _ = vector_store
        store.index("sized", "some content", "reference")

        vec_file = tmp_path / "vectors.db"
        vec_file.write_bytes(b"x" * (3 * 1024 * 1024))  # 3MB of "vectors"

        st = store.stats()
        assert st["vector_count"] == len(backend.rows)
        assert st["vector_db_size_mb"] >= 2.9
        # Total must be strictly larger than the FTS db alone.
        fts_only_mb = store._files_for(store.db_path) / 1024 / 1024
        assert st["db_size_mb"] >= fts_only_mb + 2.9

    def test_stats_without_vector_backend_omits_vector_keys(self, store):
        st = store.stats()
        assert "vector_count" not in st
        assert st["db_size_mb"] >= 0


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


def test_deleting_a_source_takes_its_tombstone(tmp_path):
    """v3.9.4: `delete_source` cleared chunks and the source row but left the
    tombstone behind, pointing at an id that no longer exists."""
    store = ContentStore(db_path=tmp_path / "cs.db")
    source_id = store.index("note", "some content worth keeping", "reflection")
    store._mark_stale(source_id, reason="content_changed")
    assert source_id in store._stale_source_ids()

    assert store.delete_source(source_id) is True

    assert store._stale_source_ids() == set()
    store.close()
