# tests/test_structural_consent.py
"""v1.7.2 — workspace-aware, consent-gated structural ingestion.

Consent is per-Workspace.id, persisted, and DEFAULTS OFF (opt-in). The security
property: switching into a workspace without consent unloads any loaded graph, so
one project's structure never leaks into another. Reading the parent folder
(PARENT) only ever happens with explicit PARENT consent.
"""
import json
import shutil
from pathlib import Path

import pytest

from conscio.content_layer import _RAG_DISABLED
from conscio.engine import ConsciousnessEngine
from conscio.structural_consent import (
    ConsentScope,
    StructuralConsent,
    consent_path,
    sync_structure,
)
from conscio.workspace import EnvClass, Workspace

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "graph_small.json"


def _workspace(root, wid="ws-a"):
    return Workspace(root=Path(root), env=EnvClass.STABLE, id=wid)


def _plant_graph(root):
    d = Path(root) / "graphify-out"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, d / "graph.json")


@pytest.fixture
def engine(tmp_path):
    e = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")
    e.content_layer._session_rag = _RAG_DISABLED
    yield e
    e.close()


# ── consent persistence ──────────────────────────────────────────────────────
class TestConsent:
    def test_default_off(self, tmp_path):
        assert StructuralConsent(tmp_path / "c.json").scope_for("any") is ConsentScope.OFF

    def test_grant_persists(self, tmp_path):
        p = tmp_path / "c.json"
        StructuralConsent(p).grant("ws1", ConsentScope.PROJECT)
        assert StructuralConsent(p).scope_for("ws1") is ConsentScope.PROJECT  # reloaded

    def test_grant_off_removes_entry(self, tmp_path):
        p = tmp_path / "c.json"
        c = StructuralConsent(p)
        c.grant("ws1", ConsentScope.PARENT)
        c.grant("ws1", ConsentScope.OFF)
        assert StructuralConsent(p).scope_for("ws1") is ConsentScope.OFF
        assert "ws1" not in json.loads(p.read_text())

    def test_corrupt_store_defaults_empty(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text("{not valid json")
        assert StructuralConsent(p).scope_for("ws1") is ConsentScope.OFF

    def test_missing_store_is_fine(self, tmp_path):
        assert StructuralConsent(tmp_path / "nope.json").scope_for("x") is ConsentScope.OFF


# ── graph path resolution ────────────────────────────────────────────────────
class TestGraphPath:
    def test_off_is_none_even_with_graph_present(self, tmp_path):
        _plant_graph(tmp_path)
        c = StructuralConsent(tmp_path / "c.json")        # default OFF
        assert c.graph_path_for(_workspace(tmp_path)) is None

    def test_project_resolves_when_present(self, tmp_path):
        _plant_graph(tmp_path)
        c = StructuralConsent(tmp_path / "c.json")
        c.grant("ws-a", ConsentScope.PROJECT)
        p = c.graph_path_for(_workspace(tmp_path))
        assert p is not None and p.name == "graph.json"

    def test_project_none_when_graph_absent(self, tmp_path):
        c = StructuralConsent(tmp_path / "c.json")
        c.grant("ws-a", ConsentScope.PROJECT)
        assert c.graph_path_for(_workspace(tmp_path)) is None

    def test_parent_reads_parent_folder(self, tmp_path):
        child = tmp_path / "proj"
        child.mkdir()
        _plant_graph(tmp_path)                              # graph at the PARENT
        c = StructuralConsent(tmp_path / "c.json")
        c.grant("ws-a", ConsentScope.PARENT)
        p = c.graph_path_for(_workspace(child))
        assert p is not None and p.parent.parent == tmp_path


# ── sync orchestration ───────────────────────────────────────────────────────
class TestSync:
    def test_loads_on_consent(self, engine, tmp_path):
        _plant_graph(tmp_path)
        c = StructuralConsent(tmp_path / "c.json")
        c.grant("ws-a", ConsentScope.PROJECT)
        status = sync_structure(engine, _workspace(tmp_path), c)
        assert status.startswith("loaded")
        assert engine.structural_signal() is not None

    def test_unloads_on_switch_to_unconsented(self, engine, tmp_path):
        _plant_graph(tmp_path)
        c = StructuralConsent(tmp_path / "c.json")
        c.grant("ws-a", ConsentScope.PROJECT)
        sync_structure(engine, _workspace(tmp_path, "ws-a"), c)
        assert engine.structural_signal() is not None
        other = tmp_path / "other"
        other.mkdir()
        status = sync_structure(engine, _workspace(other, "ws-b"), c)  # no consent
        assert status == "unloaded"
        assert engine.structural_signal() is None        # no cross-project leak

    def test_none_when_nothing_loaded_and_no_consent(self, engine, tmp_path):
        c = StructuralConsent(tmp_path / "c.json")
        assert sync_structure(engine, _workspace(tmp_path), c) == "none"

    def test_malformed_graph_unloads_and_reports(self, engine, tmp_path):
        d = tmp_path / "graphify-out"
        d.mkdir()
        (d / "graph.json").write_text('{"not": "a graph"}')
        c = StructuralConsent(tmp_path / "c.json")
        c.grant("ws-a", ConsentScope.PROJECT)
        status = sync_structure(engine, _workspace(tmp_path), c)
        assert status == "load-error"
        assert engine.structural_signal() is None         # stays safe, never crashes


# ── engine unload ────────────────────────────────────────────────────────────
class TestUnload:
    def test_unload_clears_signal_and_lookup(self, engine):
        engine.load_structure(FIXTURE)
        assert engine.structural_signal() is not None
        engine.unload_structure()
        assert engine.structural_signal() is None
        assert engine.structural_lookup("conscio_engine_reflect") is None


# ── CLI operator surface ─────────────────────────────────────────────────────
class TestCLIConsent:
    def test_grant_then_show(self, tmp_path, capsys):
        from conscio.cli import main
        assert main(["consent", "project", "--storage", str(tmp_path)]) == 0
        assert "project" in capsys.readouterr().out
        store = json.loads(consent_path(str(tmp_path)).read_text())
        assert list(store.values()) == ["project"]
        assert main(["consent", "--storage", str(tmp_path)]) == 0   # show
        assert "project" in capsys.readouterr().out

    def test_consent_path_under_storage(self, tmp_path):
        assert consent_path(str(tmp_path)) == Path(tmp_path) / "structural_consent.json"
