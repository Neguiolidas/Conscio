"""Tests for rebuild_db() — Ato 2 trigram migration."""

from pathlib import Path

from conscio.content_store import ContentStore


class TestRebuildDb:
    """Verify trigram migration to separate DB."""

    def _make_store(self, tmp_path: Path) -> ContentStore:
        db = tmp_path / "conscio.db"
        store = ContentStore(db_path=db)
        store.index("doc1", "certutil.exe download", "pentest", content_type="markdown")
        store.index("doc2", "T1569.002 PsExec lateral", "pentest", content_type="markdown")
        store.index("doc3", "bypass WAF techniques", "pentest", content_type="markdown")
        return store

    def test_rebuild_migrates_trigram(self, tmp_path):
        """rebuild_db moves trigram to separate DB."""
        store = self._make_store(tmp_path)
        # Pre: trigram in main DB
        has_before = store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_trigram'"
        ).fetchone()
        assert has_before, "chunks_trigram should exist before rebuild"

        result = store.rebuild_db()
        assert result["status"] == "ok"
        assert result["migrated"] > 0

        # Post: trigram NOT in main DB
        has_after = store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_trigram'"
        ).fetchone()
        assert not has_after, "chunks_trigram should NOT exist after rebuild"

        # Trigram DB exists
        assert (tmp_path / "conscio_trigram.db").exists()

    def test_rebuild_reduces_db_size(self, tmp_path):
        """rebuild_db does not increase main DB size (may shrink with enough data)."""
        store = self._make_store(tmp_path)
        # Index enough docs to generate meaningful trigram index overhead
        for i in range(200):
            store.index(f"doc_{i}", f"content_{i} " + "certutil.exe " * 10, "pentest")

        result = store.rebuild_db()
        assert result["status"] == "ok"
        # After rebuild, main DB should not be larger than before
        # (trigram index moved out; VACUUM reclaims freed pages)
        assert result["db_size_after"] <= result["db_size_before"], \
            f"DB should not grow: {result['db_size_before']} → {result['db_size_after']}"
        # Trigram DB should have content
        assert result["trigram_size"] > 0

    def test_rebuild_idempotent(self, tmp_path):
        """rebuild_db called twice is no-op second time."""
        store = self._make_store(tmp_path)
        result1 = store.rebuild_db()
        assert result1["status"] == "ok"

        result2 = store.rebuild_db()
        assert result2["status"] == "already_migrated"
        assert result2["migrated"] == 0

    def test_search_works_after_rebuild(self, tmp_path):
        """search() still works after rebuild (porter-only by default)."""
        store = self._make_store(tmp_path)
        store.rebuild_db()

        results = store.search("bypass WAF")
        assert len(results) > 0
        assert any("bypass" in r.content for r in results)

    def test_trigram_search_works_after_rebuild(self, tmp_path):
        """search(use_trigram=True) works via separate DB after rebuild."""
        store = self._make_store(tmp_path)
        store.rebuild_db()

        results = store.search("certutil", use_trigram=True)
        assert len(results) > 0
        assert any("certutil" in r.content for r in results)

    def test_auto_detect_works_after_rebuild(self, tmp_path):
        """Auto-detect trigram works via separate DB after rebuild."""
        store = self._make_store(tmp_path)
        store.rebuild_db()

        results = store.search("T1569.002")
        assert len(results) > 0

    def test_backup_created(self, tmp_path):
        """rebuild_db creates a .bak backup."""
        store = self._make_store(tmp_path)
        store.rebuild_db()
        assert (tmp_path / "conscio.db.bak").exists()

    def test_rowid_consistency_after_rebuild(self, tmp_path):
        """Rowids in trigram DB match rowids in main DB (for RRF merge)."""
        store = self._make_store(tmp_path)

        # Get rowids from main DB chunks (porter)
        porter_rowids = {row["rowid"] for row in store.db.execute(
            "SELECT rowid FROM chunks"
        ).fetchall()}

        store.rebuild_db()

        # Get rowids from trigram DB
        tri_path = tmp_path / "conscio_trigram.db"
        import sqlite3
        tri_conn = sqlite3.connect(str(tri_path))
        tri_rowids = {row[0] for row in tri_conn.execute(
            "SELECT rowid FROM chunks_trigram"
        ).fetchall()}
        tri_conn.close()

        # Rowids should overlap (same chunks indexed in both)
        # Note: porter and trigram share rowid space when both exist
        assert porter_rowids == tri_rowids, f"Rowid mismatch: porter={porter_rowids}, trigram={tri_rowids}"
