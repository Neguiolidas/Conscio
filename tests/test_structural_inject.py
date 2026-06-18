# tests/test_structural_inject.py
"""v1.7.1 — budget-adaptive structural injection + engine pull API.

The distiller (v1.7.0) produces a ranked signal; this slice injects it into the
LLM context (additively — cognition state untouched) sized to the context window,
and exposes engine.structural_lookup()/structural_signal() as advisory()-style
pull surfaces. Injection renders LABELS + community digests only, never raw
node-ids (so the v1.7.0 dangling-ref artifact never reaches the LLM).
"""
import json
import pathlib

import pytest

from conscio.content_layer import _RAG_DISABLED
from conscio.engine import ConsciousnessEngine
from conscio.structural import (
    StructuralDistiller,
    render_structural,
    structural_budget,
)

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "graph_small.json"


def _sig(nodes=None, hyperedges=None):
    g = {"nodes": nodes or [], "hyperedges": hyperedges or [],
         "links": [], "built_at_commit": "cafe1234"}
    return StructuralDistiller.from_dict(g).distill()


@pytest.fixture
def engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    e.content_layer._session_rag = _RAG_DISABLED
    yield e
    e.close()


# ── budget scaling ───────────────────────────────────────────────────────────
class TestStructuralBudget:
    def test_floor_for_tiny_window(self):
        assert structural_budget(2000) == 120

    def test_scales_in_small_band(self):
        assert structural_budget(8000) == 240          # 8000 * 0.03

    def test_ceiled_for_large_window(self):
        assert structural_budget(200000) == 1200

    def test_monotonic_between_floor_and_ceil(self):
        assert structural_budget(8000) < structural_budget(131000)

    def test_try_break_extreme_windows_stay_clamped(self):
        # I-B1: nonsensical / overflow windows must never escape [FLOOR, CEIL].
        for window in (-1, 0, 1, 10 ** 12):
            b = structural_budget(window)
            assert 120 <= b <= 1200, (window, b)
        assert structural_budget(-1) == 120          # negative -> floor, not negative
        assert structural_budget(10 ** 12) == 1200   # overflow -> ceil, not flood


# ── renderer ─────────────────────────────────────────────────────────────────
class TestRenderStructural:
    def test_empty_signal_renders_nothing(self):
        assert render_structural(_sig(), 1000) == ""

    def test_header_and_hyperedge_label_present(self):
        sig = _sig(hyperedges=[{"id": "h1", "label": "Act pipeline",
                                "nodes": ["x"]}])
        out = render_structural(sig, 1000)
        assert "WORKSPACE STRUCTURE" in out
        assert "Act pipeline" in out

    def test_never_leaks_raw_node_ids(self):
        # the dangling-ref guard: labels yes, node-ids never
        sig = _sig(
            nodes=[{"id": "secret_node_id", "label": "Visible", "community": 0,
                    "source_file": "f.py"}],
            hyperedges=[{"id": "h1", "label": "Edge One",
                         "nodes": ["secret_node_id", "dangling_xyz"]}],
        )
        out = render_structural(sig, 1000)
        assert "Edge One" in out and "Visible" in out
        assert "secret_node_id" not in out
        assert "dangling_xyz" not in out

    def test_budget_adaptive_fewer_lines_when_smaller(self):
        hs = [{"id": f"h{i}", "label": f"Edge{i:02d} pipeline relation",
               "nodes": ["n"]} for i in range(20)]
        ns = [{"id": f"n{i}", "label": f"Node{i}", "community": i % 3,
               "source_file": f"f{i}.py"} for i in range(12)]
        sig = _sig(nodes=ns, hyperedges=hs)
        small = render_structural(sig, 30)
        big = render_structural(sig, 2000)
        assert len(small.splitlines()) < len(big.splitlines())
        assert small  # tiny budget still yields header + at least one line

    def test_returns_empty_when_budget_below_header(self):
        sig = _sig(hyperedges=[{"id": "h1", "label": "X", "nodes": []}])
        assert render_structural(sig, 1) == ""

    def test_hyperedges_before_communities(self):
        sig = _sig(
            nodes=[{"id": "n1", "label": "L1", "community": 5,
                    "source_file": "a.py"}],
            hyperedges=[{"id": "h1", "label": "EdgeLabel", "nodes": ["n1"]}],
        )
        out = render_structural(sig, 2000)
        assert "⬡" in out and "▣" in out
        assert out.index("⬡") < out.index("▣")

    def test_community_digest_shows_labels_and_files(self):
        sig = _sig(nodes=[{"id": "n1", "label": "CircuitBreaker", "community": 0,
                           "source_file": "breaker.py"}])
        out = render_structural(sig, 2000)
        assert "CircuitBreaker" in out and "breaker.py" in out


# ── engine pull API (advisory() siblings) ────────────────────────────────────
class TestEnginePullAPI:
    def test_lookup_none_when_unloaded(self, engine):
        assert engine.structural_lookup("anything") is None

    def test_signal_none_when_unloaded(self, engine):
        assert engine.structural_signal() is None

    def test_load_returns_signal(self, engine):
        sig = engine.load_structure(FIXTURE)
        assert sig.node_count == 101
        assert len(sig.hyperedges) == 24
        assert engine.structural_signal() is sig

    def test_lookup_after_load(self, engine):
        engine.load_structure(FIXTURE)
        hit = engine.structural_lookup("conscio_engine_reflect")
        assert hit and hit["kind"] == "node"

    def test_load_bad_input_raises_valueerror(self, engine, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text('{"not": "a graph"}')
        with pytest.raises(ValueError):
            engine.load_structure(bad)


# ── additive injection (cognition untouched) ─────────────────────────────────
class TestInjection:
    def test_unloaded_injection_equals_consciousness_state(self, engine):
        # no graph -> injection is exactly the cognition state, byte for byte
        assert engine.get_state_for_injection() == engine._state.to_injection()
        assert "WORKSPACE STRUCTURE" not in engine.get_state_for_injection()

    def test_loaded_injection_appends_structure(self, engine):
        base = engine._state.to_injection()
        engine.load_structure(FIXTURE)
        full = engine.get_state_for_injection()
        assert full.startswith(base)             # cognition block unchanged, first
        assert "WORKSPACE STRUCTURE" in full
        assert "⬡" in full                       # at least one hyperedge label

    def test_injection_leaks_no_node_ids(self, engine):
        engine.load_structure(FIXTURE)
        full = engine.get_state_for_injection()
        assert "act_actpipeline_act" not in full   # a real hyperedge node id


# ── advisory() enrichment ────────────────────────────────────────────────────
class TestAdvisoryStructural:
    def test_structural_none_when_unloaded(self, engine):
        assert engine.advisory()["structural"] is None

    def test_structural_reports_after_load(self, engine):
        engine.load_structure(FIXTURE)
        s = engine.advisory()["structural"]
        assert s["loaded"] is True
        assert s["hyperedges"] == 24
        assert s["nodes"] == 101
        assert s["commit"].startswith("48f14a61")
        assert s["hash"] and s["communities"] > 0


# ── v1.8 structural drift (temporal awareness) ───────────────────────────────
_SHA1 = "1111111111111111111111111111111111111111"
_SHA2 = "2222222222222222222222222222222222222222"


def _n(nid, community=0):
    return {"id": nid, "label": nid.upper(), "community": community,
            "source_file": "a.py", "source_location": "L1", "file_type": "code"}


def _h(hid, label):
    return {"id": hid, "label": label, "nodes": ["n1"],
            "relation": "participate_in", "confidence_score": 0.9, "source_file": "a.py"}


def _write_graph(path, *, nodes=None, hyperedges=None, links=None, commit="c1"):
    path.write_text(json.dumps({
        "nodes": nodes or [], "hyperedges": hyperedges or [],
        "links": links or [], "built_at_commit": commit}))
    return path


def _event_types(engine):
    return [e.to_dict()["type"] for e in engine.event_bus.query(limit=50)]


class TestStructuralDrift:
    def test_event_type_is_valid(self):
        from conscio.event_bus import VALID_TYPES
        assert "structure:changed" in VALID_TYPES

    def test_first_load_is_first_sight(self, engine, tmp_path):
        g = _write_graph(tmp_path / "g.json", nodes=[_n("n1")],
                         hyperedges=[_h("h1", "E1")])
        engine.load_structure(g, workspace_id="wsA")
        d = engine.structural_delta()
        assert d is not None
        assert d.first_sight is True and d.changed is False

    def test_baseline_persisted(self, engine, tmp_path):
        g = _write_graph(tmp_path / "g.json", nodes=[_n("n1")])
        engine.load_structure(g, workspace_id="wsA")
        assert (engine.storage / "structural_drift.json").exists()

    def test_reload_identical_not_changed(self, engine, tmp_path):
        g = _write_graph(tmp_path / "g.json", nodes=[_n("n1")],
                         hyperedges=[_h("h1", "E1")])
        engine.load_structure(g, workspace_id="wsA")
        engine.load_structure(g, workspace_id="wsA")
        assert engine.structural_delta().changed is False

    def test_mutation_detected_with_labels(self, engine, tmp_path):
        p = tmp_path / "g.json"
        _write_graph(p, hyperedges=[_h("h1", "E1"), _h("h2", "Gone Edge")], commit="c1")
        engine.load_structure(p, workspace_id="wsA")
        _write_graph(p, hyperedges=[_h("h1", "E1")], commit="c2")   # drop h2, new commit
        engine.load_structure(p, workspace_id="wsA")
        d = engine.structural_delta()
        assert d.changed is True
        assert d.commit_changed is True
        assert "Gone Edge" in d.hyperedges_removed

    def test_structure_changed_event_emitted_on_drift(self, engine, tmp_path):
        p = tmp_path / "g.json"
        _write_graph(p, hyperedges=[_h("h1", "E1")], commit="c1")
        engine.load_structure(p, workspace_id="wsA")
        assert "structure:changed" not in _event_types(engine)      # first sight: silent
        _write_graph(p, hyperedges=[_h("h1", "E1"), _h("h2", "E2")], commit="c2")
        engine.load_structure(p, workspace_id="wsA")
        assert "structure:changed" in _event_types(engine)

    def test_advisory_carries_drift_and_freshness(self, engine, tmp_path):
        git = tmp_path / ".git"
        git.mkdir()
        (git / "HEAD").write_text(_SHA2 + "\n")                      # repo HEAD
        g = _write_graph(tmp_path / "g.json", nodes=[_n("n1")], commit=_SHA1)
        engine.load_structure(g, workspace_id="wsA", root=tmp_path)
        s = engine.advisory()["structural"]
        assert s["drift"] is not None and s["drift"]["first_sight"] is True
        assert s["freshness"]["stale"] is True                       # SHA1 != SHA2

    def test_no_workspace_id_means_no_drift(self, engine):
        engine.load_structure(FIXTURE)                               # v1.7 call shape
        assert engine.structural_delta() is None
        assert engine.structural_freshness() is None
        s = engine.advisory()["structural"]
        assert s["drift"] is None and s["freshness"] is None

    def test_freshness_none_without_root(self, engine, tmp_path):
        g = _write_graph(tmp_path / "g.json", nodes=[_n("n1")])
        engine.load_structure(g, workspace_id="wsA")                 # no root
        assert engine.structural_delta() is not None
        assert engine.structural_freshness() is None

    def test_unload_clears_drift(self, engine, tmp_path):
        g = _write_graph(tmp_path / "g.json", nodes=[_n("n1")])
        engine.load_structure(g, workspace_id="wsA", root=tmp_path)
        assert engine.structural_delta() is not None
        engine.unload_structure()
        assert engine.structural_delta() is None
        assert engine.structural_freshness() is None
