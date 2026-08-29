"""Tests for structural + knowledge endpoints in route()."""
from __future__ import annotations

from conscio.observatory.halls_view import HallsProjection
from conscio.observatory.knowledge_view import KnowledgeProjection
from conscio.observatory.liaison_view import LiaisonProjection
from conscio.observatory.projection import Projection
from conscio.observatory.server import route
from conscio.observatory.society import SocietyProjection
from conscio.observatory.structural_view import StructuralProjection


def _projections(tmp_path):
    noo = tmp_path / "noo.db"
    liai = tmp_path / "liai.db"
    sp = StructuralProjection(tmp_path)
    kp = KnowledgeProjection(tmp_path)
    proj = Projection(tmp_path)
    soc = SocietyProjection(noo)
    liai_proj = LiaisonProjection(liai)
    halls_proj = HallsProjection(liai)   # v4.5
    return proj, soc, liai_proj, sp, kp, halls_proj


def _call(method, path, query, projs, token=None, auth=None):
    proj, soc, liai, sp, kp, halls = projs
    return route(method, path, query, projection=proj, society=soc,
                 liaison=liai, halls=halls, structural=sp, knowledge=kp,
                 token=token, auth=auth)


def test_structural_drift_endpoint(tmp_path):
    resp = _call("GET", "/api/structural/drift", {}, _projections(tmp_path))
    assert resp.status == 200
    assert isinstance(resp.payload, list)


def test_structural_freshness_endpoint(tmp_path):
    resp = _call("GET", "/api/structural/freshness", {}, _projections(tmp_path))
    assert resp.status == 200
    assert "known" in resp.payload


def test_structural_graph_endpoint_no_consent(tmp_path):
    resp = _call("GET", "/api/structural/graph", {}, _projections(tmp_path))
    assert resp.status == 200
    assert resp.payload.get("available") is False


def test_knowledge_entities_endpoint_empty(tmp_path):
    resp = _call("GET", "/api/knowledge/entities", {}, _projections(tmp_path))
    assert resp.status == 200
    assert isinstance(resp.payload, list)


def test_knowledge_relationships_endpoint_empty(tmp_path):
    resp = _call("GET", "/api/knowledge/relationships", {}, _projections(tmp_path))
    assert resp.status == 200
    assert isinstance(resp.payload, list)


def test_knowledge_timeline_endpoint(tmp_path):
    resp = _call("GET", "/api/knowledge/timeline", {"entity": "foo"}, _projections(tmp_path))
    assert resp.status == 200
    assert isinstance(resp.payload, list)


def test_mutation_blocked_on_new_endpoints(tmp_path):
    resp = _call("POST", "/api/structural/graph", {}, _projections(tmp_path))
    assert resp.status == 405


def test_token_required(tmp_path):
    resp = _call("GET", "/api/structural/drift", {}, _projections(tmp_path),
                 token="secret", auth=None)
    assert resp.status == 401


def test_404_on_unknown(tmp_path):
    resp = _call("GET", "/api/nonexistent", {}, _projections(tmp_path))
    assert resp.status == 404