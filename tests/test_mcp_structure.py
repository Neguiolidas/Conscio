"""Structural cognition reachable from the MCP host (Lote 5, graphify).

The daemon syncs the consented workspace graph into the engine, but the MCP
server (the Claude Code integration) never did — so structural cognition was
dead for the primary host: it never ingested a graph and exposed no way to
query one. This wires startup sync + two read tools.
"""
import shutil
from pathlib import Path

from conscio.engine import ConsciousnessEngine
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings, _sync_structure_at_startup
from conscio.structural_consent import ConsentScope, StructuralConsent, consent_path
from conscio.workspace import EnvClass, Workspace

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "graph_small.json"


def _bindings(tmp_path):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")
    seen = SeenStore(tmp_path / "mcp_seen.db")
    return Bindings(eng, seen, adapter_name=None, workspace_id="ws"), eng, seen


def _plant(root):
    d = Path(root) / "graphify-out"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, d / "graph.json")


def test_structure_tools_registered(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        names = set(b._tools())
        assert {"conscio.structure", "conscio.structural_lookup"} <= names
        defs = {d["name"] for d in b.tool_defs()}
        assert {"conscio.structure", "conscio.structural_lookup"} <= defs
    finally:
        seen.close()
        eng.close()


def test_structure_reports_not_loaded_by_default(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        assert b._tools()["conscio.structure"]({}) == {"loaded": False}
    finally:
        seen.close()
        eng.close()


def test_structure_reports_loaded_signal(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        eng.load_structure(str(FIXTURE), workspace_id="ws", root=tmp_path)
        rep = b._tools()["conscio.structure"]({})
        assert rep["loaded"] is True
        assert rep["node_count"] > 0
        assert "digest" in rep and isinstance(rep["digest"], str)
    finally:
        seen.close()
        eng.close()


def test_structural_lookup_resolves_a_node(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        eng.load_structure(str(FIXTURE), workspace_id="ws", root=tmp_path)
        sig = eng.structural_signal()
        # community ids are always resolvable; pick the top one
        cid = str(sig.communities[0].community_id)
        res = b._tools()["conscio.structural_lookup"]({"key": cid})["result"]
        assert res is not None and res["kind"] == "community"
    finally:
        seen.close()
        eng.close()


def test_startup_sync_loads_on_consent(tmp_path):
    wa = tmp_path / "a"
    wa.mkdir()
    _plant(wa)
    ws = Workspace(root=wa, env=EnvClass.STABLE, id="ws-a")
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")
    consent = StructuralConsent(consent_path(eng.storage))
    consent.grant("ws-a", ConsentScope.PROJECT)
    try:
        status = _sync_structure_at_startup(eng, ws)
        assert status.startswith("loaded")
        assert eng.structural_signal() is not None
    finally:
        eng.close()


def test_startup_sync_noop_without_consent(tmp_path):
    wa = tmp_path / "a"
    wa.mkdir()
    _plant(wa)                                    # graph present, NOT consented
    ws = Workspace(root=wa, env=EnvClass.STABLE, id="ws-a")
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")
    try:
        _sync_structure_at_startup(eng, ws)
        assert eng.structural_signal() is None
    finally:
        eng.close()
