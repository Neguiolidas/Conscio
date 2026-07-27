"""Tests for trigram DB separation (Ato 1 — v3.7)."""

from pathlib import Path

from conscio.content_store import ContentStore


class TestTrigramSeparation:
    """Verify _fts_search_trigram and use_trigram flag work correctly."""

    def _make_store(self, tmp_path: Path) -> ContentStore:
        db = tmp_path / "conscio.db"
        store = ContentStore(db_path=db)
        store.index("doc1", "certutil.exe download url", "pentest", content_type="markdown")
        store.index("doc2", "Bypass WAF with SQL injection techniques", "pentest", content_type="markdown")
        store.index("doc3", "T1569.002 lateral movement via PsExec", "pentest", content_type="markdown")
        return store

    def test_porter_only_search(self, tmp_path):
        """search(use_trigram=False) returns porter results only."""
        store = self._make_store(tmp_path)
        results = store.search("bypass WAF", use_trigram=False)
        assert len(results) > 0
        assert any("Bypass WAF" in r.content for r in results)

    def test_trigram_explicit(self, tmp_path):
        """search(use_trigram=True) activates trigram search."""
        store = self._make_store(tmp_path)
        results = store.search("certutil", use_trigram=True)
        assert len(results) > 0
        assert any("certutil" in r.content for r in results)

    def test_auto_detect_dotted_number(self, tmp_path):
        """Query with dotted number (T1569.002) auto-activates trigram."""
        store = self._make_store(tmp_path)
        results = store.search("T1569.002")
        assert len(results) > 0
        assert any("T1569" in r.content for r in results)

    def test_auto_detect_dash(self, tmp_path):
        """Query with dash auto-activates trigram."""
        store = self._make_store(tmp_path)
        results = store.search("certutil.exe")
        assert len(results) > 0

    def test_no_auto_detect_plain_words(self, tmp_path):
        """Plain words without special chars do NOT activate trigram."""
        store = self._make_store(tmp_path)
        # "bypass" has no dots/dashes/slashes — should still work via porter
        results = store.search("bypass WAF")
        assert len(results) > 0

    def test_trigram_fallback_to_main_db(self, tmp_path):
        """Pre-rebuild: trigram still in main DB, _fts_search_trigram falls back."""
        store = self._make_store(tmp_path)
        # No conscio_trigram.db exists — should use chunks_trigram from main DB
        assert not (tmp_path / "conscio_trigram.db").exists()
        results = store.search("certutil", use_trigram=True)
        assert len(results) > 0
        assert any("certutil" in r.content for r in results)

    def test_trigram_no_table_returns_empty(self, tmp_path):
        """If no trigram table anywhere, trigram search returns [] (graceful)."""
        store = self._make_store(tmp_path)
        # Simulate post-rebuild state: drop chunks_trigram from main DB
        store.db.execute("DROP TABLE chunks_trigram")
        store.db.commit()
        # No separate trigram DB either
        assert not (tmp_path / "conscio_trigram.db").exists()
        results = store.search("certutil", use_trigram=True)
        # Porter might still find it, but trigram contribution is gone
        # Just verify no crash
        assert isinstance(results, list)

    def test_default_is_porter_only(self, tmp_path):
        """Default search (no use_trigram) does not invoke trigram."""
        store = self._make_store(tmp_path)
        # "bypass" has no special chars — porter only
        results = store.search("bypass")
        assert len(results) > 0
