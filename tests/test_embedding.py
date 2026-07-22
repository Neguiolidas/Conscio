"""TDD: EmbeddingProvider — unified wrapper."""
from conscio.embedding import EmbeddingProvider


def test_embedder_lazy_no_deps():
    """Force no network → None."""
    ep = EmbeddingProvider(force_no_network=True)
    assert ep.get_embedder() is None
    assert ep.embed("test") is None


def test_embedder_interface():
    """With injected SentenceTransformer-like mock, interface returns list[float]."""
    from unittest.mock import MagicMock
    ep = EmbeddingProvider()
    mock = MagicMock()
    # SentenceTransformer has .encode() but NOT .embed()
    del mock.embed
    mock.encode.return_value = [0.1] * 384
    ep._embedder = mock
    v = ep.embed("test")
    assert len(v) == 384


def test_embedder_dimension():
    """Default dimension is 384 (all-MiniLM-L6-v2 native fallback)."""
    ep = EmbeddingProvider()
    assert ep.default_dimension == 384


def test_embedder_available_returns_bool():
    ep = EmbeddingProvider(force_no_network=True)
    assert ep.available() is False


def test_embedder_force_returns_none():
    """If force_no_network, embed returns None."""
    ep = EmbeddingProvider(force_no_network=True)
    assert ep.embed("test") is None
    assert ep.embed_batch(["test"]) is None


def test_embedder_embed_batch_interface():
    from unittest.mock import MagicMock
    ep = EmbeddingProvider()
    mock = MagicMock()
    del mock.embed
    mock.encode.return_value = [[0.1] * 384, [0.2] * 384]
    ep._embedder = mock
    vecs = ep.embed_batch(["test1", "test2"])
    assert len(vecs) == 2
    assert all(len(v) == 384 for v in vecs)


def test_embedder_ollama_interface():
    """Ollama-style embedder (has .embed() not .encode())."""
    from unittest.mock import MagicMock
    ep = EmbeddingProvider()
    mock = MagicMock()
    del mock.encode
    mock.embed.return_value = [0.1] * 384
    mock.embed_batch.return_value = [[0.1] * 384, [0.2] * 384]
    ep._embedder = mock
    v = ep.embed("test")
    assert len(v) == 384
    vecs = ep.embed_batch(["a", "b"])
    assert len(vecs) == 2


def test_embedder_env_var_large_model(monkeypatch):
    """CONSCIO_EMBED_MODEL env var switches to 768-dim."""
    monkeypatch.setenv("CONSCIO_EMBED_MODEL", "nomic-embed-text-v1.5")
    monkeypatch.setenv("CONSCIO_EMBED_DIM", "768")
    ep = EmbeddingProvider()
    assert ep.model_name == "nomic-embed-text-v1.5"
    assert ep.default_dimension == 768
