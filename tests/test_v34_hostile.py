"""Hostile review — try to break v3.4 implementation."""
import sqlite3

from conscio.observatory.knowledge_view import KnowledgeProjection
from conscio.observatory.liaison_view import LiaisonProjection
from conscio.observatory.projection import Projection
from conscio.observatory.server import route
from conscio.observatory.society import SocietyProjection
from conscio.observatory.structural_view import StructuralProjection


def _projections(storage):
    return (Projection(storage), SocietyProjection(storage / "noo.db"),
            LiaisonProjection(storage / "liai.db"),
            StructuralProjection(storage), KnowledgeProjection(storage))


# ── StructuralProjection hostile ──

def test_drift_corrupt_json_returns_empty(tmp_path):
    from conscio.structural_drift import drift_path
    p = drift_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json {")
    proj = StructuralProjection(tmp_path)
    assert proj.drift_timeline() == []


def test_drift_huge_limit_clamped(tmp_path):
    proj = StructuralProjection(tmp_path)
    result = proj.drift_timeline(limit=99999)
    assert isinstance(result, list)


def test_freshness_nonexistent_root(tmp_path):
    proj = StructuralProjection(tmp_path)
    result = proj.freshness(root="/nonexistent/path/xyz")
    assert "known" in result
    assert isinstance(result["known"], bool)


def test_graph_missing_file(tmp_path):
    proj = StructuralProjection(tmp_path)
    result = proj.graph(root=str(tmp_path))
    assert result["available"] is False


def test_graph_corrupt_json(tmp_path):
    gpath = tmp_path / "graphify-out" / "graph.json"
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text("{ broken")
    proj = StructuralProjection(tmp_path)
    result = proj.graph(root=str(tmp_path))
    assert result["available"] is False
    assert "error" in result["reason"].lower()


# ── KnowledgeProjection hostile ──

def test_kg_corrupt_db(tmp_path):
    (tmp_path / "kg.db").write_text("not a database")
    proj = KnowledgeProjection(tmp_path)
    assert proj.entities() == []
    assert proj.relationships() == []
    assert proj.timeline(entity="x") == []


def test_kg_no_tables(tmp_path):
    conn = sqlite3.connect(tmp_path / "kg.db")
    conn.execute("CREATE TABLE bogus (x TEXT)")
    conn.commit()
    conn.close()
    proj = KnowledgeProjection(tmp_path)
    assert proj.entities() == []


# ── Server hostile ──

def test_post_blocked_on_structural(tmp_path):
    proj, soc, liai, sp, kp = _projections(tmp_path)
    resp = route("POST", "/api/structural/graph", {},
                 projection=proj, society=soc, liaison=liai,
                 structural=sp, knowledge=kp, token=None, auth=None,
                 workspace_root=None)
    assert resp.status == 405


def test_delete_blocked_on_knowledge(tmp_path):
    proj, soc, liai, sp, kp = _projections(tmp_path)
    resp = route("DELETE", "/api/knowledge/entities", {},
                 projection=proj, society=soc, liaison=liai,
                 structural=sp, knowledge=kp, token=None, auth=None,
                 workspace_root=None)
    assert resp.status == 405


def test_token_required_when_set(tmp_path):
    proj, soc, liai, sp, kp = _projections(tmp_path)
    resp = route("GET", "/api/structural/drift", {},
                 projection=proj, society=soc, liaison=liai,
                 structural=sp, knowledge=kp,
                 token="secret", auth=None,
                 workspace_root=None)
    assert resp.status == 401


def test_404_on_unknown_path(tmp_path):
    proj, soc, liai, sp, kp = _projections(tmp_path)
    resp = route("GET", "/api/nonexistent", {},
                 projection=proj, society=soc, liaison=liai,
                 structural=sp, knowledge=kp, token=None, auth=None,
                 workspace_root=None)
    assert resp.status == 404


def test_static_d3_in_whitelist():
    from conscio.observatory.server import _STATIC_WHITELIST
    assert "d3.min.js" in _STATIC_WHITELIST
