"""
Tests for CONSCIO_VECTORS auto-detect behavior (v3.6.1).

Covers:
- Auto-detect: sentence_transformers available → vectors active
- Auto-detect: sentence_transformers unavailable → FTS5-only, no crash
- CONSCIO_VECTORS=0 → force disable even if dep available
- CONSCIO_VECTORS=1 → force enable (crashes if dep unavailable)
- No env var → auto-detect kicks in
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock


def _make_engine(storage: Path, **kwargs):
    """Create engine with minimal kwargs."""
    from conscio import ConsciousnessEngine

    return ConsciousnessEngine(
        model_name="test",
        storage_path=str(storage),
        base_url="http://localhost:9999/v1",
        **kwargs,
    )


def test_auto_detect_no_dep(monkeypatch, tmp_path):
    """Without sentence_transformers, engine works FTS5-only."""
    import sys
    # Remove sentence_transformers if loaded
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CONSCIO_VECTORS", None)
        eng = _make_engine(tmp_path)
        assert eng.vector_backend is None
        assert eng.embedding_pipeline is None
        # Engine still works — content_store functional
        eng.content_store.index(label="test", content="hello world", category="system")
        results = eng.content_store.search("hello", limit=1)
        assert len(results) >= 1
        eng.close()


def test_auto_detect_with_dep(monkeypatch, tmp_path):
    """With sentence_transformers available (mocked), vectors auto-enable."""
    import sys

    # Create a fake module that exists
    fake_module = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    # Also mock the actual model loading so it doesn't hit network
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CONSCIO_VECTORS", None)
        eng = _make_engine(tmp_path)
        # vector_backend should be created since dep is "available"
        assert eng.vector_backend is not None
        assert eng.embedding_pipeline is not None
        eng.close()


def test_env_zero_disables(monkeypatch, tmp_path):
    """CONSCIO_VECTORS=0 disables even when dep is available."""
    import sys

    fake_module = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    with mock.patch.dict(os.environ, {"CONSCIO_VECTORS": "0"}):
        eng = _make_engine(tmp_path)
        assert eng.vector_backend is None
        eng.close()


def test_env_force_enable(monkeypatch, tmp_path):
    """CONSCIO_VECTORS=1 forces enable even without dep (may crash on first use)."""
    import sys

    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with mock.patch.dict(os.environ, {"CONSCIO_VECTORS": "1"}):
        # Construction should still work — lazy probe
        eng = _make_engine(tmp_path)
        # vector_backend is created (it doesn't import sentence_transformers)
        assert eng.vector_backend is not None
        eng.close()


def test_no_env_auto_detect(monkeypatch, tmp_path):
    """No env var + no dep → auto-detect falls back to FTS5-only."""
    import sys

    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CONSCIO_VECTORS", None)
        eng = _make_engine(tmp_path)
        assert eng.vector_backend is None
        # But engine is fully functional
        eng.content_store.index(label="t2", content="foo bar", category="system")
        assert len(eng.content_store.search("foo", limit=1)) >= 1
        eng.close()
