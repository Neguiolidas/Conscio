"""Tests for ContentLayerManager — k validation, recall, perceive, session_rag, layer_sort."""
import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass

from conscio.content_layer import (
    ContentLayerManager,
    ContentLayer,
    MAX_K,
    LAYER_EPSILON,
    layer_of,
    layer_sort_key,
)


# ── Test doubles ──────────────────────────────────────────────────────────

@dataclass
class _FTSResult:
    """Mimics ContentStore.SearchResult for layer_sort_key."""
    source_category: str = "system"
    content_type: str = "prose"
    rank: float = 1.0
    content: str = ""


@dataclass
class _RAGResult:
    """Mimics SessionRAG.SearchResult."""
    content: str = ""
    score: float = 0.0


class _StubContentStore:
    """Minimal ContentStore stub with .search()."""
    def __init__(self, results=None):
        self._results = results or []

    def search(self, query, limit=10, category=None):
        return self._results[:limit]


class _StubWorldModel:
    """Minimal WorldModel stub that records add_entity calls."""
    def __init__(self):
        self.entities = {}

    def add_entity(self, name=None, entity_type="unknown", attributes=None, state=""):
        self.entities[name] = {
            "type": entity_type,
            "attributes": attributes,
            "state": state,
        }


class _StubSessionRAG:
    """Minimal SessionRAG stub with .available() and .search()."""
    def __init__(self, available=True, search_results=None):
        self._available = available
        self._search_results = search_results or []

    def available(self):
        return self._available

    def search(self, query, limit=10):
        return self._search_results[:limit]


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def manager():
    return ContentLayerManager(
        content_store=_StubContentStore(),
        world_model=_StubWorldModel(),
        session_rag_provider=None,
    )


@pytest.fixture
def world_model():
    return _StubWorldModel()


@pytest.fixture
def manager_with_world(world_model):
    return ContentLayerManager(
        content_store=_StubContentStore(),
        world_model=world_model,
        session_rag_provider=None,
    )


# ══════════════════════════════════════════════════════════════════════════
# k parameter validation
# ══════════════════════════════════════════════════════════════════════════

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
        result = manager.recall("test query", k=MAX_K)
        assert isinstance(result, list)

    def test_k_one_is_accepted(self, manager):
        result = manager.recall("test query", k=1)
        assert isinstance(result, list)

    def test_k_default_is_accepted(self, manager):
        result = manager.recall("test query")
        assert isinstance(result, list)


# ══════════════════════════════════════════════════════════════════════════
# recall()
# ══════════════════════════════════════════════════════════════════════════

class TestRecallEmptyQuery:
    """recall() with empty/blank query returns empty list."""

    def test_empty_string(self, manager):
        assert manager.recall("") == []

    def test_whitespace_only(self, manager):
        assert manager.recall("   \t  ") == []

    def test_none_like_empty(self, manager):
        # Empty string is falsy
        assert manager.recall("") == []


class TestRecallFTS5Results:
    """recall() returns FTS5 results with layer reorder applied."""

    def test_results_returned(self):
        results = [
            _FTSResult(content="routine item", source_category="system", rank=10.0),
            _FTSResult(content="processing item", source_category="reflection", rank=8.0),
        ]
        mgr = ContentLayerManager(
            content_store=_StubContentStore(results=results),
            world_model=_StubWorldModel(),
            session_rag_provider=None,
        )
        out = mgr.recall("test", k=5)
        assert len(out) == 2
        assert "routine item" in out[0] or "processing item" in out[0]

    def test_layer_reorder_prioritizes_processing(self):
        """PROCESSING items should be promoted over ROUTINE at same bucket."""
        # Two results in the same epsilon bucket, PROCESSING should sort first
        eps = LAYER_EPSILON
        results = [
            _FTSResult(content="routine", source_category="system", rank=2 * eps),
            _FTSResult(content="processing", source_category="reflection", rank=2 * eps),
        ]
        mgr = ContentLayerManager(
            content_store=_StubContentStore(results=results),
            world_model=_StubWorldModel(),
            session_rag_provider=None,
        )
        out = mgr.recall("test", k=5)
        # After layer_sort_key, processing should come before routine
        assert out[0] == "processing"
        assert out[1] == "routine"


class TestRecallCategoriesFilter:
    """recall() passes categories filter to content_store.search()."""

    def test_categories_passed_to_search(self):
        store = _StubContentStore(results=[])
        # Wrap to track calls
        calls = []
        original_search = store.search

        def tracking_search(query, limit=10, category=None):
            calls.append({"query": query, "limit": limit, "category": category})
            return original_search(query, limit=limit, category=category)

        store.search = tracking_search

        mgr = ContentLayerManager(
            content_store=store,
            world_model=_StubWorldModel(),
            session_rag_provider=None,
        )
        mgr.recall("test", categories=["reflection", "consciousness"])
        # Should call search once per category
        assert len(calls) == 2
        assert calls[0]["category"] == "reflection"
        assert calls[1]["category"] == "consciousness"

    def test_no_categories_uses_default_search(self):
        store = _StubContentStore(results=[])
        calls = []
        original_search = store.search

        def tracking_search(query, limit=10, category=None):
            calls.append({"query": query, "category": category})
            return original_search(query, limit=limit, category=category)

        store.search = tracking_search

        mgr = ContentLayerManager(
            content_store=store,
            world_model=_StubWorldModel(),
            session_rag_provider=None,
        )
        mgr.recall("test")
        # Single call without category
        assert len(calls) == 1
        assert calls[0]["category"] is None


class TestRecallDeduplication:
    """recall() deduplicates snippets with same 80-char prefix."""

    def test_duplicate_prefix_deduplicated(self):
        base = "A" * 80  # 80-char prefix
        results = [
            _FTSResult(content=base + " unique part 1", source_category="system", rank=5.0),
            _FTSResult(content=base + " unique part 2", source_category="system", rank=4.0),
        ]
        mgr = ContentLayerManager(
            content_store=_StubContentStore(results=results),
            world_model=_StubWorldModel(),
            session_rag_provider=None,
        )
        out = mgr.recall("test", k=10)
        # Only the first should survive dedup (same 80-char prefix)
        assert len(out) == 1

    def test_different_prefixes_both_kept(self):
        results = [
            _FTSResult(content="Alpha prefix content here", source_category="system", rank=5.0),
            _FTSResult(content="Beta prefix content here", source_category="system", rank=4.0),
        ]
        mgr = ContentLayerManager(
            content_store=_StubContentStore(results=results),
            world_model=_StubWorldModel(),
            session_rag_provider=None,
        )
        out = mgr.recall("test", k=10)
        assert len(out) == 2


class TestRecallMaxSnippetsCapped:
    """recall() caps results at k."""

    def test_snippets_capped_at_k(self):
        results = [
            _FTSResult(content=f"snippet {i}", source_category="system", rank=float(10 - i))
            for i in range(10)
        ]
        mgr = ContentLayerManager(
            content_store=_StubContentStore(results=results),
            world_model=_StubWorldModel(),
            session_rag_provider=None,
        )
        out = mgr.recall("test", k=3)
        assert len(out) == 3


class TestRecallSessionRAGNone:
    """recall() falls back to FTS5 only when SessionRAG provider returns None."""

    def test_provider_returns_none(self):
        results = [
            _FTSResult(content="fts result", source_category="system", rank=5.0),
        ]
        mgr = ContentLayerManager(
            content_store=_StubContentStore(results=results),
            world_model=_StubWorldModel(),
            session_rag_provider=lambda: None,
        )
        out = mgr.recall("test", k=5)
        assert out == ["fts result"]


class TestRecallSessionRAGRaises:
    """recall() falls back gracefully when SessionRAG raises exception."""

    def test_session_rag_search_raises(self):
        """SessionRAG.search() raising should not crash recall."""
        results = [
            _FTSResult(content="fts result", source_category="system", rank=5.0),
        ]
        bad_rag = MagicMock()
        bad_rag.search.side_effect = RuntimeError("Ollama down")

        mgr = ContentLayerManager(
            content_store=_StubContentStore(results=results),
            world_model=_StubWorldModel(),
            session_rag_provider=lambda: bad_rag,
        )
        out = mgr.recall("test", k=5)
        # Should still return FTS5 results
        assert out == ["fts result"]

    def test_session_rag_provider_raises(self):
        """Provider itself raising should not crash recall."""
        results = [
            _FTSResult(content="fts result", source_category="system", rank=5.0),
        ]

        def bad_provider():
            raise RuntimeError("Cannot create SessionRAG")

        mgr = ContentLayerManager(
            content_store=_StubContentStore(results=results),
            world_model=_StubWorldModel(),
            session_rag_provider=bad_provider,
        )
        out = mgr.recall("test", k=5)
        # Should still return FTS5 results
        assert out == ["fts result"]


class TestRecallSessionRAGMerged:
    """recall() merges SessionRAG semantic hits with FTS5 results."""

    def test_session_rag_hits_merged(self):
        fts_results = [
            _FTSResult(content="fts hit", source_category="system", rank=5.0),
        ]
        rag_results = [
            _RAGResult(content="semantic hit", score=0.9),
        ]
        rag = _StubSessionRAG(available=True, search_results=rag_results)

        mgr = ContentLayerManager(
            content_store=_StubContentStore(results=fts_results),
            world_model=_StubWorldModel(),
            session_rag_provider=lambda: rag,
        )
        out = mgr.recall("test", k=5)
        assert "fts hit" in out
        assert "semantic hit" in out
        assert len(out) == 2


class TestRecallContentStoreFails:
    """recall() is resilient when ContentStore.search() raises."""

    def test_content_store_search_raises(self):
        store = MagicMock()
        store.search.side_effect = RuntimeError("DB locked")

        mgr = ContentLayerManager(
            content_store=store,
            world_model=_StubWorldModel(),
            session_rag_provider=None,
        )
        # Should not raise, should return empty list
        out = mgr.recall("test", k=5)
        assert isinstance(out, list)


# ══════════════════════════════════════════════════════════════════════════
# perceive()
# ══════════════════════════════════════════════════════════════════════════

class TestPerceive:
    """perceive() updates world_model with entities."""

    def test_updates_world_model_with_entities(self, manager_with_world, world_model):
        entities = {
            "BTC": {"type": "asset", "state": "bullish", "attributes": {"price": 70000}},
            "portfolio": {"type": "account", "state": "profitable"},
        }
        manager_with_world.perceive("market is up", entities=entities)
        assert "BTC" in world_model.entities
        assert world_model.entities["BTC"]["type"] == "asset"
        assert world_model.entities["BTC"]["state"] == "bullish"
        assert world_model.entities["BTC"]["attributes"] == {"price": 70000}
        assert "portfolio" in world_model.entities
        assert world_model.entities["portfolio"]["type"] == "account"

    def test_empty_entities_dict_handled(self, manager_with_world, world_model):
        manager_with_world.perceive("world state text", entities={})
        # Should not add any entities
        assert len(world_model.entities) == 0

    def test_none_entities_handled(self, manager_with_world, world_model):
        manager_with_world.perceive("world state text", entities=None)
        # Should not add any entities
        assert len(world_model.entities) == 0

    def test_entity_with_missing_type_defaults_to_unknown(self, manager_with_world, world_model):
        entities = {"foo": {"state": "active"}}
        manager_with_world.perceive("state", entities=entities)
        assert world_model.entities["foo"]["type"] == "unknown"

    def test_entity_with_missing_state_defaults_to_empty(self, manager_with_world, world_model):
        entities = {"bar": {"type": "widget"}}
        manager_with_world.perceive("state", entities=entities)
        assert world_model.entities["bar"]["state"] == ""

    def test_entity_with_missing_attributes_defaults_to_none(self, manager_with_world, world_model):
        entities = {"baz": {"type": "widget", "state": "ok"}}
        manager_with_world.perceive("state", entities=entities)
        assert world_model.entities["baz"]["attributes"] is None


# ══════════════════════════════════════════════════════════════════════════
# session_rag property
# ══════════════════════════════════════════════════════════════════════════

class TestSessionRAGProperty:
    """session_rag property: lazy init, caching, error handling."""

    def test_lazy_init_called_once(self):
        """Provider is called only once (cached after first access)."""
        call_count = 0
        rag_instance = _StubSessionRAG(available=True)

        def provider():
            nonlocal call_count
            call_count += 1
            return rag_instance

        mgr = ContentLayerManager(
            content_store=_StubContentStore(),
            world_model=_StubWorldModel(),
            session_rag_provider=provider,
        )
        # Access twice
        _ = mgr.session_rag
        _ = mgr.session_rag
        assert call_count == 1  # Only called once, then cached

    def test_no_provider_returns_none(self):
        """No provider means session_rag is None."""
        mgr = ContentLayerManager(
            content_store=_StubContentStore(),
            world_model=_StubWorldModel(),
            session_rag_provider=None,
        )
        assert mgr.session_rag is None

    def test_provider_returns_none(self):
        """Provider returning None means session_rag is None."""
        mgr = ContentLayerManager(
            content_store=_StubContentStore(),
            world_model=_StubWorldModel(),
            session_rag_provider=lambda: None,
        )
        assert mgr.session_rag is None

    def test_provider_raises_returns_none(self):
        """Provider raising means session_rag is None (via graceful failure)."""
        def bad_provider():
            raise RuntimeError("Ollama not available")

        mgr = ContentLayerManager(
            content_store=_StubContentStore(),
            world_model=_StubWorldModel(),
            session_rag_provider=bad_provider,
        )
        # The property itself doesn't catch exceptions, but recall() does.
        # Accessing session_rag when provider raises will propagate the exception.
        # This is expected: the property is not wrapped in try/except.
        with pytest.raises(RuntimeError, match="Ollama not available"):
            _ = mgr.session_rag


# ══════════════════════════════════════════════════════════════════════════
# layer_sort_key()
# ══════════════════════════════════════════════════════════════════════════

class TestLayerSortKey:
    """layer_sort_key() orders: PROCESSING > INTUITION > ROUTINE within bucket."""

    def test_processing_before_routine_same_bucket(self):
        """PROCESSING sorts before ROUTINE in the same epsilon bucket."""
        processing = _FTSResult(source_category="reflection", rank=1.0)
        routine = _FTSResult(source_category="system", rank=1.0)
        assert layer_sort_key(processing) < layer_sort_key(routine)

    def test_intuition_before_routine_same_bucket(self):
        """INTUITION sorts before ROUTINE in the same epsilon bucket."""
        intuition = _FTSResult(source_category="error", rank=1.0)
        routine = _FTSResult(source_category="system", rank=1.0)
        assert layer_sort_key(intuition) < layer_sort_key(routine)

    def test_processing_before_intuition_same_bucket(self):
        """PROCESSING sorts before INTUITION in the same epsilon bucket."""
        processing = _FTSResult(source_category="reflection", rank=1.0)
        intuition = _FTSResult(source_category="error", rank=1.0)
        assert layer_sort_key(processing) < layer_sort_key(intuition)

    def test_higher_rank_wins_across_buckets(self):
        """A high-rank ROUTINE result is NOT buried by a low-rank PROCESSING one."""
        high_routine = _FTSResult(source_category="system", rank=100.0)
        low_processing = _FTSResult(source_category="reflection", rank=0.001)
        assert layer_sort_key(high_routine) < layer_sort_key(low_processing)

    def test_exact_rank_tiebreak_within_bucket(self):
        """Within same bucket and layer, higher rank sorts first."""
        a = _FTSResult(source_category="system", rank=2.0)
        b = _FTSResult(source_category="system", rank=1.0)
        assert layer_sort_key(a) < layer_sort_key(b)


# ══════════════════════════════════════════════════════════════════════════
# layer_of() helper
# ══════════════════════════════════════════════════════════════════════════

class TestLayerOf:
    """layer_of() maps categories and content_types to ContentLayer."""

    def test_reflection_is_processing(self):
        assert layer_of("reflection") == ContentLayer.PROCESSING

    def test_consciousness_is_processing(self):
        assert layer_of("consciousness") == ContentLayer.PROCESSING

    def test_error_is_intuition(self):
        assert layer_of("error") == ContentLayer.INTUITION

    def test_system_is_routine(self):
        assert layer_of("system") == ContentLayer.ROUTINE

    def test_trading_is_routine(self):
        assert layer_of("trading") == ContentLayer.ROUTINE

    def test_perception_is_routine(self):
        assert layer_of("perception") == ContentLayer.ROUTINE

    def test_session_is_routine(self):
        assert layer_of("session") == ContentLayer.ROUTINE

    def test_external_is_routine(self):
        assert layer_of("external") == ContentLayer.ROUTINE

    def test_unrecognized_category_metric_type_is_routine(self):
        assert layer_of("unknown_cat", content_type="metric") == ContentLayer.ROUTINE

    def test_unrecognized_category_log_type_is_routine(self):
        assert layer_of("unknown_cat", content_type="log") == ContentLayer.ROUTINE

    def test_unrecognized_category_prose_type_defaults_processing(self):
        assert layer_of("unknown_cat", content_type="prose") == ContentLayer.PROCESSING

    def test_unrecognized_category_no_type_defaults_processing(self):
        assert layer_of("totally_unknown") == ContentLayer.PROCESSING
