# tests/test_structural_inject.py
"""v1.7.1 — budget-adaptive structural injection + engine pull API.

The distiller (v1.7.0) produces a ranked signal; this slice injects it into the
LLM context (additively — cognition state untouched) sized to the context window,
and exposes engine.structural_lookup()/structural_signal() as advisory()-style
pull surfaces. Injection renders LABELS + community digests only, never raw
node-ids (so the v1.7.0 dangling-ref artifact never reaches the LLM).
"""
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
