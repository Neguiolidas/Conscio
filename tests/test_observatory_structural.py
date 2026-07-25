"""Tests for StructuralProjection — read-only drift + freshness + graph views."""
from __future__ import annotations

import json
from pathlib import Path

from conscio.observatory.structural_view import StructuralProjection


def _make_drift_store(storage: Path, entries: dict) -> Path:
    """Write a fake drift store JSON at the expected path.

    entries is a dict[ws_id, dict] matching StructuralDigest.to_json() shape:
    {commit, content_hash, node_count, link_count, hyperedges, communities, seen_at}
    """
    from conscio.structural_drift import drift_path
    p = drift_path(storage)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries))
    return p


def test_drift_timeline_empty_when_no_store(tmp_path):
    proj = StructuralProjection(tmp_path)
    result = proj.drift_timeline(limit=20)
    assert result == []


def test_drift_timeline_returns_entries(tmp_path):
    entries = {
        "ws-1": {
            "commit": "abc123",
            "content_hash": "hash1",
            "node_count": 42,
            "link_count": 10,
            "hyperedges": {},
            "communities": {},
            "seen_at": "2026-07-25T10:00:00",
        },
        "ws-2": {
            "commit": "def456",
            "content_hash": "hash2",
            "node_count": 17,
            "link_count": 5,
            "hyperedges": {},
            "communities": {},
            "seen_at": "2026-07-25T11:00:00",
        },
    }
    _make_drift_store(tmp_path, entries)
    proj = StructuralProjection(tmp_path)
    result = proj.drift_timeline(limit=20)
    assert len(result) == 2
    # ordenado por seen_at DESC
    assert result[0]["workspace_id"] == "ws-2"
    assert result[1]["workspace_id"] == "ws-1"
    assert result[0]["node_count"] == 17


def test_drift_timeline_respects_limit(tmp_path):
    entries = {}
    for i in range(5):
        ws = f"ws-{i}"
        entries[ws] = {
            "commit": f"commit-{i}",
            "content_hash": f"hash-{i}",
            "node_count": i,
            "link_count": i,
            "hyperedges": {},
            "communities": {},
            "seen_at": f"2026-07-25T{10+i}:00:00",
        }
    _make_drift_store(tmp_path, entries)
    proj = StructuralProjection(tmp_path)
    result = proj.drift_timeline(limit=3)
    assert len(result) == 3


def test_freshness_returns_unknown_when_no_root(tmp_path):
    proj = StructuralProjection(tmp_path)
    result = proj.freshness()
    assert result["known"] is False
    assert "graph_commit" in result
    assert "head_commit" in result


def test_freshness_unknown_when_no_store(tmp_path):
    proj = StructuralProjection(tmp_path)
    result = proj.freshness(root="/tmp")
    assert result["known"] is False


def test_graph_returns_unavailable_when_no_file(tmp_path):
    proj = StructuralProjection(tmp_path)
    result = proj.graph(root=str(tmp_path))
    assert result["available"] is False
    assert "not found" in result["reason"]


def test_graph_returns_data_when_present(tmp_path):
    gpath = tmp_path / "graphify-out" / "graph.json"
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text(json.dumps({"nodes": [], "hyperedges": []}))
    proj = StructuralProjection(tmp_path)
    result = proj.graph(root=str(tmp_path))
    assert result["available"] is True
    assert "data" in result


def test_graph_corrupt_json(tmp_path):
    gpath = tmp_path / "graphify-out" / "graph.json"
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text("{ broken")
    proj = StructuralProjection(tmp_path)
    result = proj.graph(root=str(tmp_path))
    assert result["available"] is False
    assert "error" in result["reason"].lower()
