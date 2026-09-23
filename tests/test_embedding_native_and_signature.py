"""TDD and Adversarial Tests for Conscio v4.7 Task 5:
Embedding native-only policy and vector space signature validation.

Covers:
1. Native-only policy by default (unset / empty / "native"):
   - sentence_transformers in-process ONLY
   - ZERO network probes (no Ollama port 11434, no OpenAI/LM Studio port 1234)
   - Running Ollama/LM Studio daemon must NOT alter native default (Mutant 5)
   - Missing/failing native model does NOT fall back to network daemons
2. Explicit opt-in backends:
   - "ollama" uses Ollama only
   - "openai" uses OpenAI-compat only
   - "auto" uses legacy fallback with WARNING in logs
   - Invalid backend raises ValueError at the boundary
3. semantic.py uses EmbeddingProvider instead of direct OllamaEmbedder
4. Vector-space signature persistence and validation in VectorBackend:
   - Schema vector_space_meta (key PK, value) storing backend, model, dimension, version
   - Signature persisted on first write
   - Signature mismatch (backend, model, dimension, version) rejects ingest/query
     before touching vectors (Mutant 6)
   - Hostile: no mutation occurs on mismatch
"""
from __future__ import annotations

import logging
from unittest import mock

import pytest

from conscio.embedding import (
    DEFAULT_DIMENSION,
    DEFAULT_MODEL,
    EmbeddingProvider,
)
from conscio.semantic import SemanticEngine
from conscio.vector_backend import VectorBackend

# ── 1. Native-Only Default & Adversarial Daemons (Mutant 5) ─────────────


def test_native_default_backend_unset(monkeypatch):
    """Default when CONSCIO_EMBED_BACKEND is unset is 'native'."""
    monkeypatch.delenv("CONSCIO_EMBED_BACKEND", raising=False)
    ep = EmbeddingProvider()
    assert ep.backend == "native"
    assert ep.model_name == DEFAULT_MODEL
    assert ep.default_dimension == DEFAULT_DIMENSION


def test_native_default_backend_empty(monkeypatch):
    """Empty CONSCIO_EMBED_BACKEND normalizes to 'native'."""
    monkeypatch.setenv("CONSCIO_EMBED_BACKEND", "")
    ep = EmbeddingProvider()
    assert ep.backend == "native"


def _fake_st_module(monkeypatch, encode_side_effect=None):
    """Inject a fake sentence_transformers into sys.modules.

    mock.patch("sentence_transformers.SentenceTransformer") fails with
    ModuleNotFoundError when the real package is absent (CI installs only
    the light deps). Injecting a fake module keeps the contract test
    runnable everywhere: the code under test does
    ``from sentence_transformers import SentenceTransformer`` at call time.
    """
    import sys
    import types
    fake = types.ModuleType("sentence_transformers")

    class _FakeST:
        def __init__(self, name, *a, **k):
            if encode_side_effect is not None:
                raise encode_side_effect
            self._name = name

        def encode(self, text, *a, **k):
            return [0.5] * 384

    fake.SentenceTransformer = _FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    return fake


def test_native_default_never_probes_network_even_if_daemons_run(monkeypatch):
    """Mutant 5: Ollama/LM Studio running on local ports must NOT be probed or selected.

    Native default MUST NOT attempt to instantiate or probe OllamaEmbedder or
    OpenAICompatibleEmbedder, even when socket/HTTP connects would succeed.
    """
    monkeypatch.delenv("CONSCIO_EMBED_BACKEND", raising=False)

    ollama_mock = mock.MagicMock()
    ollama_mock.embed.return_value = [0.1] * 384
    openai_mock = mock.MagicMock()
    openai_mock.embed.return_value = [0.2] * 384

    with mock.patch("conscio.session_rag.OllamaEmbedder", return_value=ollama_mock) as patch_ollama, \
         mock.patch("conscio.session_rag.OpenAICompatibleEmbedder", return_value=openai_mock) as patch_openai:
        ep = EmbeddingProvider()
        # Inject a fake sentence_transformers so native succeeds — the
        # real package is optional (CI does not install heavy deps).
        _fake_st_module(monkeypatch)
        embedder = ep.get_embedder()
        assert embedder is not None
        assert ep.active_backend == "native"

        # Neither Ollama nor OpenAI was probed or instantiated!
        assert patch_ollama.call_count == 0
        assert patch_openai.call_count == 0


def test_native_default_missing_fails_clearly_without_network_fallback(monkeypatch):
    """If sentence_transformers is missing or fails in native mode, it returns None.

    It must NOT fall back to Ollama or OpenAI daemon!
    """
    monkeypatch.delenv("CONSCIO_EMBED_BACKEND", raising=False)

    ollama_mock = mock.MagicMock()
    ollama_mock.embed.return_value = [0.1] * 384

    with mock.patch("conscio.session_rag.OllamaEmbedder", return_value=ollama_mock) as patch_ollama:
        ep = EmbeddingProvider()
        # Simulate the package being broken/missing at import time inside
        # the native path — the provider must fail clearly, no fallback.
        _fake_st_module(monkeypatch, encode_side_effect=ImportError("No module"))
        embedder = ep.get_embedder()
        assert embedder is None
        assert ep.embed("test") is None

        # Even though native failed, Ollama was NEVER probed!
        assert patch_ollama.call_count == 0


# ── 2. Explicit Opt-in Backends ──────────────────────────────────────────


def test_explicit_optin_ollama(monkeypatch):
    """CONSCIO_EMBED_BACKEND=ollama explicitly selects Ollama."""
    monkeypatch.setenv("CONSCIO_EMBED_BACKEND", "ollama")
    ep = EmbeddingProvider()
    assert ep.backend == "ollama"

    ollama_mock = mock.MagicMock()
    ollama_mock.embed.return_value = [0.1] * 384

    with mock.patch("conscio.session_rag.OllamaEmbedder", return_value=ollama_mock) as patch_ollama:
        embedder = ep.get_embedder()
        assert embedder is ollama_mock
        assert ep.active_backend == "ollama"
        assert patch_ollama.call_count == 1


def test_explicit_optin_openai(monkeypatch):
    """CONSCIO_EMBED_BACKEND=openai explicitly selects OpenAI / LM Studio."""
    monkeypatch.setenv("CONSCIO_EMBED_BACKEND", "openai")
    ep = EmbeddingProvider()
    assert ep.backend == "openai"

    openai_mock = mock.MagicMock()
    openai_mock.embed.return_value = [0.1] * 384

    with mock.patch("conscio.session_rag.OpenAICompatibleEmbedder", return_value=openai_mock) as patch_openai:
        embedder = ep.get_embedder()
        assert embedder is openai_mock
        assert ep.active_backend == "openai"
        assert patch_openai.call_count == 1


def test_explicit_optin_auto_logs_warning(monkeypatch, caplog):
    """CONSCIO_EMBED_BACKEND=auto uses legacy fallback and logs WARNING."""
    monkeypatch.setenv("CONSCIO_EMBED_BACKEND", "auto")
    ep = EmbeddingProvider()
    assert ep.backend == "auto"

    ollama_mock = mock.MagicMock()
    ollama_mock.embed.return_value = [0.1] * 384

    with caplog.at_level(logging.WARNING), mock.patch(
        "conscio.session_rag.OllamaEmbedder", return_value=ollama_mock
    ):
        embedder = ep.get_embedder()
        assert embedder is ollama_mock
        assert ep.active_backend == "ollama"

    # WARNING must be logged mentioning auto and chosen backend
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("auto" in w.lower() and "ollama" in w.lower() for w in warnings)


def test_unknown_backend_raises_at_boundary(monkeypatch):
    """Unknown CONSCIO_EMBED_BACKEND raises ValueError at initialization."""
    monkeypatch.setenv("CONSCIO_EMBED_BACKEND", "invalid_backend")
    with pytest.raises(ValueError, match="Unknown CONSCIO_EMBED_BACKEND"):
        EmbeddingProvider()

    with pytest.raises(ValueError, match="Unknown CONSCIO_EMBED_BACKEND"):
        EmbeddingProvider(backend="foo")


# ── 3. semantic.py uses EmbeddingProvider ────────────────────────────────


def test_semantic_engine_uses_embedding_provider_not_direct_ollama():
    """semantic.py must use EmbeddingProvider, never instantiating OllamaEmbedder directly."""
    import inspect

    import conscio.semantic as sem

    # Inspect source of semantic.py to ensure OllamaEmbedder is not imported or instantiated
    source = inspect.getsource(sem)
    assert "OllamaEmbedder" not in source, "semantic.py must not reference OllamaEmbedder directly"

    # SemanticEngine should use EmbeddingProvider when embedder is None
    with mock.patch("conscio.embedding.EmbeddingProvider") as mock_ep_cls:
        mock_ep_instance = mock.MagicMock()
        mock_ep_instance.embed.return_value = [0.1] * 384
        mock_ep_instance.available.return_value = True
        mock_ep_cls.return_value = mock_ep_instance

        engine = SemanticEngine()
        assert engine.available() is True
        mock_ep_cls.assert_called_once()


# ── 4. Vector Space Signature Persistence & Schema ──────────────────────


def test_vector_space_signature_persisted_on_first_write(tmp_path):
    """VectorBackend creates vector_space_meta and persists signature on first write."""
    db_path = tmp_path / "vec.db"
    sig = {
        "backend": "native",
        "model": "all-MiniLM-L6-v2",
        "dimension": 4,
        "version": "1.0",
    }
    vb = VectorBackend(db_path=db_path, signature=sig)
    # Before write, get_signature is None
    assert vb.get_signature() is None

    # First write persists signature
    vb.add("doc1", [1.0, 0.0, 0.0, 0.0])
    stored_sig = vb.get_signature()
    assert stored_sig is not None
    assert stored_sig["backend"] == "native"
    assert stored_sig["model"] == "all-MiniLM-L6-v2"
    assert stored_sig["dimension"] == 4
    assert stored_sig["version"] == "1.0"
    vb.close()


def test_vector_space_signature_empty_db_search_returns_empty(tmp_path):
    """search() on a completely empty DB returns [] without error."""
    db_path = tmp_path / "vec.db"
    vb = VectorBackend(db_path=db_path, dimension=4)
    assert vb.search([1.0, 0.0, 0.0, 0.0]) == []
    vb.close()


# ── 5. Vector Space Signature Mismatch Rejections (Mutant 6) ─────────────


def test_vector_space_signature_mismatch_adversarial(tmp_path):
    """Mutant 6: Incompatible signature rejects ingest and query BEFORE mutating vectors."""
    db_path = tmp_path / "vec.db"
    canonical_sig = {
        "backend": "native",
        "model": "all-MiniLM-L6-v2",
        "dimension": 4,
        "version": "1.0",
    }
    vb = VectorBackend(db_path=db_path, signature=canonical_sig)
    vb.add("doc1", [1.0, 0.0, 0.0, 0.0])
    vb.close()

    # 1. Incompatible Backend
    vb_bad_backend = VectorBackend(
        db_path=db_path,
        signature={
            "backend": "ollama",
            "model": "all-MiniLM-L6-v2",
            "dimension": 4,
            "version": "1.0",
        },
    )
    with pytest.raises(ValueError, match="Incompatible vector space signature"):
        vb_bad_backend.search([1.0, 0.0, 0.0, 0.0])
    with pytest.raises(ValueError, match="Incompatible vector space signature"):
        vb_bad_backend.add("doc_bad", [0.0, 1.0, 0.0, 0.0])
    with pytest.raises(ValueError, match="Incompatible vector space signature"):
        vb_bad_backend.add_batch([("doc_batch", [0.0, 1.0, 0.0, 0.0])])
    vb_bad_backend.close()

    # 2. Incompatible Model
    vb_bad_model = VectorBackend(
        db_path=db_path,
        signature={
            "backend": "native",
            "model": "nomic-embed-text-v1.5",
            "dimension": 4,
            "version": "1.0",
        },
    )
    with pytest.raises(ValueError, match="Incompatible vector space signature"):
        vb_bad_model.search([1.0, 0.0, 0.0, 0.0])
    with pytest.raises(ValueError, match="Incompatible vector space signature"):
        vb_bad_model.add("doc_bad", [0.0, 1.0, 0.0, 0.0])
    vb_bad_model.close()

    # 3. Incompatible Dimension
    vb_bad_dim = VectorBackend(
        db_path=db_path,
        signature={
            "backend": "native",
            "model": "all-MiniLM-L6-v2",
            "dimension": 8,
            "version": "1.0",
        },
    )
    with pytest.raises(ValueError, match="Incompatible vector space signature"):
        vb_bad_dim.search([1.0] * 8)
    with pytest.raises(ValueError, match="Incompatible vector space signature"):
        vb_bad_dim.add("doc_bad", [1.0] * 8)
    vb_bad_dim.close()

    # 4. Incompatible Version
    vb_bad_ver = VectorBackend(
        db_path=db_path,
        signature={
            "backend": "native",
            "model": "all-MiniLM-L6-v2",
            "dimension": 4,
            "version": "2.0",
        },
    )
    with pytest.raises(ValueError, match="Incompatible vector space signature"):
        vb_bad_ver.search([1.0, 0.0, 0.0, 0.0])
    with pytest.raises(ValueError, match="Incompatible vector space signature"):
        vb_bad_ver.add("doc_bad", [0.0, 1.0, 0.0, 0.0])
    vb_bad_ver.close()

    # CRITICAL: Verify NO mutation happened — vector store is untainted!
    vb_verify = VectorBackend(db_path=db_path, signature=canonical_sig)
    assert vb_verify.stats()["vectors"] == 1
    res = vb_verify.search([1.0, 0.0, 0.0, 0.0], limit=5)
    assert len(res) == 1
    assert res[0]["id"] == "doc1"
    vb_verify.close()
