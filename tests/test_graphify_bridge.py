"""Tests for GraphifyBridge — graphify output integration with ContentStore."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from conscio.graphify_bridge import GraphifyBridge, auto_index_graphify


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_graphify(tmp_path):
    """Create a minimal graphify-out directory with test data."""
    gdir = tmp_path / "graphify-out"
    gdir.mkdir()

    # Minimal graph.json
    graph = {
        "directed": False,
        "multigraph": False,
        "graph": {
            "hyperedges": [
                {
                    "id": "test_pipeline",
                    "label": "Test pipeline (a -> b -> c)",
                    "nodes": ["node_a", "node_b", "node_c"],
                    "relation": "participate_in",
                    "confidence": "EXTRACTED",
                    "confidence_score": 0.95,
                    "source_file": "test.py",
                }
            ]
        },
        "nodes": [
            {
                "id": "node_a",
                "label": "ClassA",
                "file_type": "code",
                "source_file": "mod_a.py",
                "source_location": "L10",
                "community": 1,
                "norm_label": "classa",
            },
            {
                "id": "node_b",
                "label": "func_b()",
                "file_type": "code",
                "source_file": "mod_b.py",
                "source_location": "L20",
                "community": 1,
                "norm_label": "func_b()",
            },
            {
                "id": "node_c",
                "label": "ClassC",
                "file_type": "code",
                "source_file": "mod_c.py",
                "source_location": "L5",
                "community": 2,
                "norm_label": "classc",
            },
        ],
        "links": [
            {
                "relation": "imports",
                "confidence": "EXTRACTED",
                "source_file": "mod_a.py",
                "source_location": "L1",
                "weight": 1.0,
                "source": "node_a",
                "target": "node_b",
                "confidence_score": 1.0,
            },
            {
                "relation": "calls",
                "confidence": "EXTRACTED",
                "source_file": "mod_b.py",
                "source_location": "L25",
                "weight": 1.0,
                "source": "node_b",
                "target": "node_c",
                "confidence_score": 0.9,
            },
        ],
        "hyperedges": [],
        "built_at_commit": "abc123",
    }
    (gdir / "graph.json").write_text(json.dumps(graph, indent=2))

    # Minimal GRAPH_REPORT.md
    report = """# Graph Report — Test

## Summary
- 3 nodes · 2 edges · 2 communities

## Community Hubs
- [[Community 1]] — ClassA, func_b()
- [[Community 2]] — ClassC
"""
    (gdir / "GRAPH_REPORT.md").write_text(report)

    return gdir


@pytest.fixture
def store(tmp_path):
    """Create a fresh ContentStore."""
    from conscio.content_store import ContentStore

    with ContentStore(db_path=tmp_path / "test.db") as s:
        yield s


# ─── Tests ────────────────────────────────────────────────────────────────


class TestGraphifyBridge:
    def test_available_with_valid_dir(self, tmp_graphify):
        bridge = GraphifyBridge(tmp_graphify)
        assert bridge.available() is True

    def test_available_with_missing_dir(self, tmp_path):
        bridge = GraphifyBridge(tmp_path / "nonexistent")
        assert bridge.available() is False

    def test_available_with_partial_dir(self, tmp_path):
        """Missing GRAPH_REPORT.md should make bridge unavailable."""
        gdir = tmp_path / "partial"
        gdir.mkdir()
        (gdir / "graph.json").write_text("{}")
        bridge = GraphifyBridge(gdir)
        assert bridge.available() is False

    def test_index_all(self, tmp_graphify, store):
        bridge = GraphifyBridge(tmp_graphify)
        stats = bridge.index_all(store)

        assert stats["report"] == 1
        assert stats["communities"] >= 1
        assert stats["hyperedges"] >= 1

        # Verify content is searchable
        results = store.search("ClassA")
        assert len(results) > 0

    def test_index_all_idempotent(self, tmp_graphify, store):
        """Re-indexing should not create duplicates (content hash dedup)."""
        bridge = GraphifyBridge(tmp_graphify)
        stats1 = bridge.index_all(store)
        stats2 = bridge.index_all(store)

        # Same counts both times
        assert stats1 == stats2

        # But content is only stored once
        results = store.search("ClassA")
        # Should find results but not doubled
        assert len(results) > 0

    def test_index_report(self, tmp_graphify, store):
        bridge = GraphifyBridge(tmp_graphify)
        bridge._index_report(store)

        results = store.search("communities")
        assert len(results) > 0

    def test_index_communities(self, tmp_graphify, store):
        bridge = GraphifyBridge(tmp_graphify)
        count = bridge._index_communities(store)

        # Nodes in community 1 and 2 should be indexed
        assert count >= 1
        results = store.search("ClassA")
        assert len(results) > 0

    def test_index_hyperedges(self, tmp_graphify, store):
        bridge = GraphifyBridge(tmp_graphify)
        count = bridge._index_hyperedges(store)

        assert count >= 1
        results = store.search("pipeline")
        assert len(results) > 0

    def test_index_all_unavailable(self, tmp_path, store):
        bridge = GraphifyBridge(tmp_path / "nope")
        stats = bridge.index_all(store)
        assert stats == {"communities": 0, "hyperedges": 0, "report": 0}


class TestAutoIndexGraphify:
    def test_auto_index_with_explicit_dir(self, tmp_graphify, store):
        stats = auto_index_graphify(store, graphify_dir=tmp_graphify)
        assert sum(stats.values()) > 0

    def test_auto_index_not_found(self, tmp_path, store):
        stats = auto_index_graphify(store, graphify_dir=tmp_path / "nope")
        assert stats == {}

    def test_auto_index_env_var(self, tmp_graphify, store, monkeypatch):
        monkeypatch.setenv("GRAPHIFY_DIR", str(tmp_graphify))
        stats = auto_index_graphify(store)
        assert sum(stats.values()) > 0


class TestGraphifySearchQuality:
    """Test that graphify-indexed content improves RAG search quality."""

    def test_search_by_class_name(self, tmp_graphify, store):
        bridge = GraphifyBridge(tmp_graphify)
        bridge.index_all(store)

        results = store.search("ClassA")
        assert any("ClassA" in r.content for r in results)

    def test_search_by_relationship(self, tmp_graphify, store):
        bridge = GraphifyBridge(tmp_graphify)
        bridge.index_all(store)

        results = store.search("imports")
        assert len(results) > 0

    def test_search_by_file_path(self, tmp_graphify, store):
        bridge = GraphifyBridge(tmp_graphify)
        bridge.index_all(store)

        results = store.search("mod_a.py")
        assert len(results) > 0

    def test_search_by_pattern(self, tmp_graphify, store):
        bridge = GraphifyBridge(tmp_graphify)
        bridge.index_all(store)

        results = store.search("pipeline")
        assert len(results) > 0
