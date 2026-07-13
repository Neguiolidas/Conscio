"""Tests for OpenAI-compatible embedding — generic embedder for LM Studio, vLLM, llama.cpp, etc."""
import json
import sqlite3
from unittest.mock import patch, MagicMock

import numpy as np

from conscio.session_rag import (
    OpenAICompatibleEmbedder, OllamaEmbedder, SessionVectorStore, Chunk,
)


# ── OpenAI-compatible embedder ──

class TestOpenAICompatibleEmbedder:
    """Test the generic OpenAI-compatible embedder."""

    def test_init_defaults(self):
        e = OpenAICompatibleEmbedder()
        assert e.model == "text-embedding-nomic-embed-text-v1.5"
        assert "127.0.0.1" in e.url or "localhost" in e.url
        assert e.dim == 768

    def test_init_custom_url(self):
        e = OpenAICompatibleEmbedder(
            url="http://localhost:1234/v1/embeddings",
            model="my-model",
            dim=384,
        )
        assert e.url == "http://localhost:1234/v1/embeddings"
        assert e.model == "my-model"
        assert e.dim == 384

    def test_embed_uses_openai_format(self):
        """Verify the request payload matches OpenAI embedding API format."""
        e = OpenAICompatibleEmbedder(
            url="http://fake:9999/v1/embeddings",
            model="test-model",
            api_key="sk-test",
        )
        captured = {}

        def fake_urlopen(req, timeout=30):
            captured["payload"] = json.loads(req.data)
            captured["url"] = req.full_url
            captured["headers"] = dict(req.headers)
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "data": [{"embedding": [0.1, 0.2, 0.3]}],
            }).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = e.embed("hello world")

        assert result == [0.1, 0.2, 0.3]
        assert captured["payload"]["model"] == "test-model"
        assert captured["payload"]["input"] == "hello world"
        assert "Authorization" in captured["headers"]

    def test_embed_truncates_long_text(self):
        e = OpenAICompatibleEmbedder(url="http://fake:9999/v1/embeddings")
        captured = {}

        def fake_urlopen(req, timeout=30):
            captured["payload"] = json.loads(req.data)
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "data": [{"embedding": [0.1]}],
            }).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        long_text = "x" * 5000
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            e.embed(long_text)

        assert len(captured["payload"]["input"]) <= 4000

    def test_embed_returns_empty_on_error(self):
        e = OpenAICompatibleEmbedder(url="http://fake:9999/v1/embeddings")
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = e.embed("test")
        assert result == []

    def test_embed_batch(self):
        e = OpenAICompatibleEmbedder(url="http://fake:9999/v1/embeddings")
        call_count = 0

        def fake_urlopen(req, timeout=30):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "data": [{"embedding": [float(call_count)]}],
            }).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            results = e.embed_batch(["a", "b", "c"])

        assert len(results) == 3
        assert call_count == 3

    def test_embed_with_api_key(self):
        e = OpenAICompatibleEmbedder(
            url="http://fake:9999/v1/embeddings",
            api_key="sk-test-key",
        )
        captured = {}

        def fake_urlopen(req, timeout=30):
            captured["auth"] = req.get_header("Authorization")
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "data": [{"embedding": [0.5]}],
            }).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            e.embed("test")

        assert captured["auth"] == "Bearer sk-test-key"

    def test_embed_without_api_key(self):
        e = OpenAICompatibleEmbedder(
            url="http://fake:9999/v1/embeddings",
            api_key="",
        )
        captured = {}

        def fake_urlopen(req, timeout=30):
            captured["auth"] = req.get_header("Authorization")
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "data": [{"embedding": [0.5]}],
            }).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            e.embed("test")

        # No auth header when no key
        assert captured["auth"] is None


# ── OllamaEmbedder backward compat ──

class TestOllamaEmbedderCompat:
    """OllamaEmbedder still works as before (Ollama API format)."""

    def test_ollama_uses_ollama_format(self):
        e = OllamaEmbedder(url="http://fake:11434/api/embeddings", model="nomic-embed-text")
        captured = {}

        def fake_urlopen(req, timeout=30):
            captured["payload"] = json.loads(req.data)
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "embedding": [0.1, 0.2],
            }).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = e.embed("test")

        assert result == [0.1, 0.2]
        assert captured["payload"]["prompt"] == "test"
        assert "model" in captured["payload"]

    def test_ollama_is_not_openai_format(self):
        """Ollama uses 'prompt' not 'input'."""
        e = OllamaEmbedder()
        captured = {}

        def fake_urlopen(req, timeout=30):
            captured["payload"] = json.loads(req.data)
            resp = MagicMock()
            resp.read.return_value = json.dumps({"embedding": [1.0]}).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            e.embed("test")

        assert "prompt" in captured["payload"]
        assert "input" not in captured["payload"]


# ── Store-level dimension safety (the corruption/crash fixes live here) ──

class TestStoreDimSafety:
    """The vector store is the integrity boundary: wrong-dim vectors are dropped
    on write, skipped on search, and a backend change triggers a re-index."""

    def _chunk(self, cid, emb):
        return Chunk(id=cid, session_id="s", role="user", content=cid, embedding=emb)

    def test_upsert_drops_wrong_dim_vector(self, tmp_path):
        store = SessionVectorStore(tmp_path / "r.db", dim=8)
        store.upsert_batch([self._chunk("bad", [0.1, 0.2, 0.3, 0.4])])  # 4 != 8
        # Chunk text kept, vector dropped (NULL) -> not searchable, never crashes.
        assert store.get_stats()["total_chunks"] == 1
        assert store.search([0.0] * 7 + [1.0], limit=5, min_score=-1.0) == []

    def test_search_survives_mixed_dim_blobs(self, tmp_path):
        store = SessionVectorStore(tmp_path / "r.db", dim=8)
        good = np.ones(8, dtype=np.float32).tolist()
        store.upsert_batch([self._chunk("good", good)])
        # Inject a rogue 4-dim blob directly (simulating a pre-fix corrupted store).
        conn = sqlite3.connect(str(store.db_path))
        conn.execute(
            "INSERT INTO chunks (id, session_id, role, content, embedding) "
            "VALUES (?,?,?,?,?)",
            ("rogue", "s", "user", "rogue", np.ones(4, dtype=np.float32).tobytes()),
        )
        conn.commit()
        conn.close()
        results = store.search(good, limit=5, min_score=-1.0)  # must not raise
        assert any(r.chunk_id == "good" for r in results)
        assert all(r.chunk_id != "rogue" for r in results)

    def test_reindex_on_model_change(self, tmp_path):
        db = tmp_path / "r.db"
        s1 = SessionVectorStore(db, dim=8, embed_model="model-A")
        s1.upsert_batch([self._chunk("c", np.ones(8, dtype=np.float32).tolist())])
        assert s1.reindex_required is False
        # Reopen with a different model -> stale -> embeddings cleared for re-index.
        s2 = SessionVectorStore(db, dim=8, embed_model="model-B")
        assert s2.reindex_required is True
        assert s2.get_stats()["total_chunks"] == 1          # text kept
        assert s2.search(np.ones(8, dtype=np.float32).tolist(), min_score=-1.0) == []

    def test_reindex_on_dim_change(self, tmp_path):
        db = tmp_path / "r.db"
        SessionVectorStore(db, dim=8, embed_model="m")
        s2 = SessionVectorStore(db, dim=16, embed_model="m")
        assert s2.reindex_required is True

    def test_first_build_records_identity_without_reindex(self, tmp_path):
        s = SessionVectorStore(tmp_path / "r.db", dim=8, embed_model="model-A")
        assert s.reindex_required is False

    def test_legacy_store_without_embed_model_is_back_compatible(self, tmp_path):
        s = SessionVectorStore(tmp_path / "r.db", dim=8)  # no embed_model
        assert s.reindex_required is False
