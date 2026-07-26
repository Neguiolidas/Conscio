"""Tests for /api/projects endpoint — scans workspace root for graphify-out/graph.json."""
from __future__ import annotations

import json

from conscio.observatory.knowledge_view import KnowledgeProjection
from conscio.observatory.liaison_view import LiaisonProjection
from conscio.observatory.projection import Projection
from conscio.observatory.server import route
from conscio.observatory.society import SocietyProjection
from conscio.observatory.structural_view import StructuralProjection


def _projections(tmp_path):
    return (Projection(tmp_path), SocietyProjection(tmp_path / "noo.db"),
            LiaisonProjection(tmp_path / "liai.db"),
            StructuralProjection(tmp_path), KnowledgeProjection(tmp_path))


def _call(method, path, query, projs, workspace_root=None, token=None, auth=None):
    proj, soc, liai, sp, kp = projs
    return route(method, path, query, projection=proj, society=soc,
                 liaison=liai, structural=sp, knowledge=kp,
                 token=token, auth=auth, workspace_root=workspace_root)


def _make_project(root, name, nodes=5, links=2):
    gdir = root / name / "graphify-out"
    gdir.mkdir(parents=True, exist_ok=True)
    graph = {"nodes": [{"id": f"n{i}", "label": f"node{i}"} for i in range(nodes)],
             "links": [{"source": "n0", "target": "n1"} for _ in range(links)]}
    (gdir / "graph.json").write_text(json.dumps(graph))


def test_projects_empty_when_no_graphify(tmp_path):
    resp = _call("GET", "/api/projects", {}, _projections(tmp_path),
                 workspace_root=str(tmp_path))
    assert resp.status == 200
    assert resp.payload == []


def test_projects_finds_subdirs_with_graph_json(tmp_path):
    _make_project(tmp_path, "alpha", nodes=10, links=3)
    _make_project(tmp_path, "beta", nodes=5, links=1)
    resp = _call("GET", "/api/projects", {}, _projections(tmp_path),
                 workspace_root=str(tmp_path))
    assert resp.status == 200
    assert len(resp.payload) == 2
    names = {p["name"] for p in resp.payload}
    assert names == {"alpha", "beta"}


def test_projects_reports_node_counts(tmp_path):
    _make_project(tmp_path, "alpha", nodes=42, links=7)
    resp = _call("GET", "/api/projects", {}, _projections(tmp_path),
                 workspace_root=str(tmp_path))
    assert resp.status == 200
    proj = resp.payload[0]
    assert proj["node_count"] == 42
    assert proj["link_count"] == 7
    assert proj["has_graph"] is True


def test_projects_skips_dirs_without_graph_json(tmp_path):
    _make_project(tmp_path, "alpha", nodes=3, links=1)
    (tmp_path / "empty").mkdir()
    resp = _call("GET", "/api/projects", {}, _projections(tmp_path),
                 workspace_root=str(tmp_path))
    assert resp.status == 200
    assert len(resp.payload) == 1
    assert resp.payload[0]["name"] == "alpha"


def test_projects_no_workspace_root_returns_empty(tmp_path):
    resp = _call("GET", "/api/projects", {}, _projections(tmp_path))
    assert resp.status == 200
    assert resp.payload == []


def test_projects_path_traversal_blocked(tmp_path):
    resp = _call("GET", "/api/projects", {}, _projections(tmp_path),
                 workspace_root="/etc")
    assert resp.status == 200
    assert resp.payload == []


def test_project_graph_serves_json(tmp_path):
    _make_project(tmp_path, "alpha", nodes=3, links=1)
    resp = _call("GET", "/api/projects/alpha/graph", {}, _projections(tmp_path),
                 workspace_root=str(tmp_path))
    assert resp.status == 200
    assert "nodes" in resp.payload
    assert len(resp.payload["nodes"]) == 3


def test_project_graph_404_when_no_project(tmp_path):
    resp = _call("GET", "/api/projects/nonexistent/graph", {},
                 _projections(tmp_path), workspace_root=str(tmp_path))
    assert resp.status == 404
