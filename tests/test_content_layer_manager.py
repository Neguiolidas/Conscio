"""Tests for ContentLayerManager — k parameter validation and layer logic."""
import pytest

from conscio.content_layer import ContentLayerManager, MAX_K


# ── Minimal stubs so ContentLayerManager works without real DBs ──

class _StubContentStore:
    """Minimal ContentStore stub with .search()."""
    def search(self, query, limit=10, category=None):
        return []


class _StubWorldModel:
    """Minimal WorldModel stub."""
    def add_entity(self, **kw):
        pass


@pytest.fixture
def manager():
    return ContentLayerManager(
        content_store=_StubContentStore(),
        world_model=_StubWorldModel(),
        session_rag_provider=None,
    )


# ── k parameter validation ──

class TestKValidation:
    """recall() must reject invalid k values with ValueError."""

    def test_k_zero_raises(self, manager):
        with pytest.raises(ValueError, match="k"):
            manager.recall("test query", k=0)

    def test_k_negative_raises(self, manager):
        with pytest.raises(ValueError, match="k"):
            manager.recall("test query", k=-1)

    def test_k_exceeds_max_raises(self, manager):
        with pytest.raises(ValueError, match="k"):
            manager.recall("test query", k=MAX_K + 1)

    def test_k_at_max_is_accepted(self, manager):
        # k == MAX_K should NOT raise — boundary is inclusive
        result = manager.recall("test query", k=MAX_K)
        assert isinstance(result, list)

    def test_k_one_is_accepted(self, manager):
        # k == 1 is the minimum valid value
        result = manager.recall("test query", k=1)
        assert isinstance(result, list)

    def test_k_default_is_accepted(self, manager):
        # default k=3 should work fine
        result = manager.recall("test query")
        assert isinstance(result, list)
