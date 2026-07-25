"""Tests for /graph endpoint — serves graphify-out/graph.html from workspace."""
from __future__ import annotations

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


def _call(method, path, query, projs, token=None, auth=None):
    proj, soc, liai, sp, kp = projs
    return route(method, path, query, projection=proj, society=soc,
                 liaison=liai, structural=sp, knowledge=kp,
                 token=token, auth=auth)


def test_graph_view_serves_html(tmp_path):
    # create graphify-out/graph.html in the workspace
    gpath = tmp_path / "graphify-out" / "graph.html"
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text("<html><body>graph here</body></html>")
    resp = _call("GET", "/graph", {"root": str(tmp_path)}, _projections(tmp_path))
    assert resp.status == 200
    assert b"graph here" in resp.body


def test_graph_view_404_when_no_file(tmp_path):
    resp = _call("GET", "/graph", {"root": str(tmp_path)}, _projections(tmp_path))
    assert resp.status == 404


def test_graph_view_post_blocked(tmp_path):
    resp = _call("POST", "/graph", {"root": str(tmp_path)}, _projections(tmp_path))
    assert resp.status == 405


def test_graph_view_path_traversal_blocked(tmp_path):
    resp = _call("GET", "/graph", {"root": "/etc"}, _projections(tmp_path))
    # /etc/graphify-out/graph.html should not exist
    assert resp.status == 404
