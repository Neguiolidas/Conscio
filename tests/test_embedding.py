"""TDD: EmbeddingProvider — unified wrapper."""
from conscio.embedding import EmbeddingProvider


def test_embedder_lazy_no_deps():
    """Force no network → None."""
    ep = EmbeddingProvider(force_no_network=True)
    assert ep.get_embedder() is None
    assert ep.embed("test") is None


def test_embedder_interface():
    """With injected mock, interface returns list[float]."""
    from unittest.mock import MagicMock
    ep = EmbeddingProvider()
    mock = MagicMock()
    mock.embed.return_value = [0.1] * 768
    ep._embedder = mock
    v = ep.embed("test")
    assert len(v) == 768


def test_embedder_dimension():
    """Default dimension is 768 (nomic-embed-text-v1.5)."""
    ep = EmbeddingProvider()
    assert ep.default_dimension == 768


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
    mock.embed.return_value = [0.1] * 768
    mock.embed_batch.return_value = [[0.1] * 768, [0.2] * 768]
    ep._embedder = mock
    vecs = ep.embed_batch(["test1", "test2"])
    assert len(vecs) == 2
    assert all(len(v) == 768 for v in vecs)
