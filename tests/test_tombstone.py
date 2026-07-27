"""Tests for tombstone tracking (v3.6.1)."""
from __future__ import annotations

from pathlib import Path

from conscio.content_store import ContentStore


def _make_store(tmp_path: Path) -> ContentStore:
    return ContentStore(db_path=tmp_path / "test.db")


def test_tombstone_table_exists(tmp_path):
    """source_tombstones table is created on init."""
    store = _make_store(tmp_path)
    rows = store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='source_tombstones'"
    ).fetchall()
    assert len(rows) == 1
    store.close()


def test_mark_stale_creates_tombstone(tmp_path):
    """_mark_stale inserts a row in source_tombstones."""
    store = _make_store(tmp_path)
    store.index(label="doc1", content="hello world", category="system")
    source_id = store.db.execute("SELECT id FROM sources WHERE label='doc1'").fetchone()["id"]
    store._mark_stale(source_id, "content_changed")
    tombstones = store.list_tombstones()
    assert len(tombstones) == 1
    assert tombstones[0]["source_id"] == source_id
    assert tombstones[0]["reason"] == "content_changed"
    store.close()


def test_search_excludes_stale_by_default(tmp_path):
    """search() does not return chunks from tombstoned sources."""
    store = _make_store(tmp_path)
    store.index(label="active_doc", content="unique keyword alpha", category="system")
    store.index(label="stale_doc", content="unique keyword beta", category="system")

    # Confirm both are searchable
    results = store.search("unique keyword", limit=10)
    assert len(results) == 2

    # Tombstone the second source
    stale_id = store.db.execute("SELECT id FROM sources WHERE label='stale_doc'").fetchone()["id"]
    store._mark_stale(stale_id)

    # search() should now exclude stale chunks
    results = store.search("unique keyword", limit=10)
    assert len(results) == 1
    assert "alpha" in results[0].content
    store.close()


def test_search_includes_stale_with_flag(tmp_path):
    """search(include_stale=True) returns chunks from tombstoned sources."""
    store = _make_store(tmp_path)
    store.index(label="keep", content=" searchable term gamma", category="system")
    store.index(label="stale", content="searchable term delta", category="system")

    stale_id = store.db.execute("SELECT id FROM sources WHERE label='stale'").fetchone()["id"]
    store._mark_stale(stale_id)

    results = store.search("searchable term", limit=10, include_stale=True)
    assert len(results) == 2
    store.close()


def test_list_tombstones_format(tmp_path):
    """list_tombstones returns dicts with source_id, reason, created_at, label."""
    store = _make_store(tmp_path)
    store.index(label="mydoc", content="content here", category="system")
    sid = store.db.execute("SELECT id FROM sources WHERE label='mydoc'").fetchone()["id"]
    store._mark_stale(sid, "file_deleted")

    ts = store.list_tombstones()
    assert len(ts) == 1
    assert ts[0]["label"] == "mydoc"
    assert ts[0]["reason"] == "file_deleted"
    assert "created_at" in ts[0]
    store.close()


def test_reingest_after_tombstone(tmp_path):
    """Re-indexing a tombstoned source creates new chunks; stale ones stay filtered."""
    store = _make_store(tmp_path)
    store.index(label="redoc", content="original content zeta", category="system")
    sid = store.db.execute("SELECT id FROM sources WHERE label='redoc'").fetchone()["id"]
    store._mark_stale(sid)

    # Re-ingest with new content (creates new source)
    store.index(label="redoc", content="updated content zeta", category="system")

    # search should return only the new (non-stale) chunk
    results = store.search("zeta", limit=10)
    assert len(results) == 1
    assert "updated" in results[0].content
    store.close()
