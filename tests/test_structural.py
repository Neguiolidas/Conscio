# tests/test_structural.py
"""v1.7.0 StructuralDistiller — Graphify-format graph.json -> compact ranked signal.

R10 governs this module: imported cognition is DATA, never code. These tests pin
the pure data layer (projection, ranking, lookup, provenance, safety guards).
Precise assertions use synthetic in-memory dicts; one test exercises the real
trimmed fixture through from_path().
"""
import json
import pathlib

import pytest

from conscio.structural import (
    CommunitySummary,
    GraphNode,
    Hyperedge,
    StructuralDistiller,
    StructuralError,
    StructuralSignal,
)

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "graph_small.json"


def _node(nid, label="n", community=0, source_file="a.py", loc="L1", ftype="code"):
    return {"id": nid, "label": label, "community": community,
            "source_file": source_file, "source_location": loc, "file_type": ftype}


def _hyper(hid="h1", label="H", nodes=("n1", "n2"), relation="participate_in",
           score=0.9, source_file="a.py"):
    return {"id": hid, "label": label, "nodes": list(nodes), "relation": relation,
            "confidence_score": score, "source_file": source_file}


def _graph(nodes=None, links=None, hyperedges=None, commit="abc123"):
    return {"nodes": nodes or [], "links": links or [],
            "hyperedges": hyperedges or [], "built_at_commit": commit}


# ── projection ────────────────────────────────────────────────────────────────
class TestProjection:
    def test_node_projection(self):
        g = _graph(nodes=[_node("n1", label="Foo.bar", community=2,
                                 source_file="x.py", loc="L9")])
        sig = StructuralDistiller.from_dict(g).distill()
        assert sig.node_count == 1
        # node detail comes back via lookup (signal carries summaries, not raw nodes)
        d = StructuralDistiller.from_dict(g).lookup("n1")
        assert d["label"] == "Foo.bar"
        assert d["source_file"] == "x.py"
        assert d["source_location"] == "L9"
        assert d["community"] == 2

    def test_hyperedge_projection_1to1(self):
        hs = [_hyper("h1", "Pipeline A", ("a", "b", "c"), score=0.95),
              _hyper("h2", "Pipeline B", ("d", "e"))]
        sig = StructuralDistiller.from_dict(_graph(hyperedges=hs)).distill()
        assert len(sig.hyperedges) == 2
        assert all(isinstance(h, Hyperedge) for h in sig.hyperedges)
        h1 = sig.hyperedges[0]
        assert h1.id == "h1" and h1.label == "Pipeline A"
        assert h1.nodes == ("a", "b", "c")        # tuple, order preserved
        assert h1.confidence_score == 0.95

    def test_hyperedge_order_preserved(self):
        hs = [_hyper("z"), _hyper("a"), _hyper("m")]
        sig = StructuralDistiller.from_dict(_graph(hyperedges=hs)).distill()
        assert [h.id for h in sig.hyperedges] == ["z", "a", "m"]

    def test_counts(self):
        g = _graph(nodes=[_node("n1"), _node("n2")],
                   links=[{"source": "n1", "target": "n2"}],
                   hyperedges=[_hyper()])
        sig = StructuralDistiller.from_dict(g).distill()
        assert sig.node_count == 2
        assert sig.link_count == 1


# ── community ranking + summaries ───────────────────────────────────────────────
class TestCommunityRankingSummary:
    def test_ranked_by_size_desc(self):
        nodes = ([_node(f"a{i}", community=1) for i in range(2)]
                 + [_node(f"b{i}", community=2) for i in range(5)]
                 + [_node(f"c{i}", community=3) for i in range(3)])
        sig = StructuralDistiller.from_dict(_graph(nodes=nodes)).distill()
        assert [c.community_id for c in sig.communities] == [2, 3, 1]
        assert [c.size for c in sig.communities] == [5, 3, 2]

    def test_size_tiebreak_by_community_id_asc(self):
        nodes = ([_node(f"a{i}", community=7) for i in range(2)]
                 + [_node(f"b{i}", community=3) for i in range(2)])
        sig = StructuralDistiller.from_dict(_graph(nodes=nodes)).distill()
        assert [c.community_id for c in sig.communities] == [3, 7]

    def test_all_communities_returned(self):
        nodes = [_node(f"n{i}", community=i) for i in range(6)]
        sig = StructuralDistiller.from_dict(_graph(nodes=nodes)).distill()
        assert len(sig.communities) == 6

    def test_summary_labels_and_files_bounded(self):
        nodes = [_node(f"n{i}", label=f"L{i}", community=0,
                       source_file=f"f{i}.py") for i in range(10)]
        sig = StructuralDistiller.from_dict(_graph(nodes=nodes)).distill()
        c = sig.communities[0]
        assert c.size == 10
        assert len(c.top_labels) <= 5
        assert len(c.files) <= 5
        assert isinstance(c, CommunitySummary)

    def test_summary_files_deduped(self):
        nodes = [_node(f"n{i}", community=0, source_file="same.py") for i in range(4)]
        sig = StructuralDistiller.from_dict(_graph(nodes=nodes)).distill()
        assert sig.communities[0].files == ("same.py",)

    def test_node_without_community_skipped_from_summaries(self):
        nodes = [_node("n1", community=None), _node("n2", community=5)]
        sig = StructuralDistiller.from_dict(_graph(nodes=nodes)).distill()
        assert [c.community_id for c in sig.communities] == [5]
        assert sig.node_count == 2  # still counted overall


# ── lookup (pure data layer) ────────────────────────────────────────────────────
class TestLookup:
    def _d(self):
        g = _graph(
            nodes=[_node("n1", label="Node One", community=4)],
            hyperedges=[_hyper("h1", "Edge One", ("n1",))],
        )
        return StructuralDistiller.from_dict(g)

    def test_lookup_node(self):
        d = self._d().lookup("n1")
        assert d["kind"] == "node"
        assert d["label"] == "Node One"

    def test_lookup_hyperedge(self):
        d = self._d().lookup("h1")
        assert d["kind"] == "hyperedge"
        assert d["nodes"] == ["n1"] or tuple(d["nodes"]) == ("n1",)

    def test_lookup_community_by_int_string(self):
        d = self._d().lookup("4")
        assert d["kind"] == "community"
        assert d["community_id"] == 4
        assert d["size"] == 1

    def test_lookup_miss_returns_none(self):
        assert self._d().lookup("nope") is None

    def test_node_id_wins_over_community(self):
        # a node literally named "4" must resolve as a node, not community 4
        g = _graph(nodes=[_node("4", label="weird", community=4)])
        assert StructuralDistiller.from_dict(g).lookup("4")["kind"] == "node"


# ── malformed / non-graph input ─────────────────────────────────────────────────
class TestMalformed:
    def test_non_graph_json_raises(self):
        with pytest.raises(StructuralError):
            StructuralDistiller.from_dict({"foo": "bar"})

    def test_structural_error_is_valueerror(self):
        # callers that `except ValueError` must keep catching it
        with pytest.raises(ValueError):
            StructuralDistiller.from_dict({"foo": "bar"})

    def test_nodes_only_is_valid(self):
        sig = StructuralDistiller.from_dict(_graph(nodes=[_node("n1")])).distill()
        assert sig.node_count == 1

    def test_hyperedges_only_is_valid(self):
        sig = StructuralDistiller.from_dict(_graph(hyperedges=[_hyper()])).distill()
        assert len(sig.hyperedges) == 1

    def test_bad_node_item_skipped_not_crash(self):
        g = _graph(nodes=[_node("n1"), "garbage", {"no_id": 1}, _node("n2")])
        sig = StructuralDistiller.from_dict(g).distill()
        assert sig.node_count == 2  # only the two well-formed nodes

    def test_nodes_wrong_type_raises(self):
        with pytest.raises(StructuralError):
            StructuralDistiller.from_dict({"nodes": "notalist", "hyperedges": []})


# ── size guards (OOM protection) ────────────────────────────────────────────────
class TestSizeGuards:
    def test_max_nodes_exceeded_raises(self):
        nodes = [_node(f"n{i}") for i in range(10)]
        with pytest.raises(StructuralError):
            StructuralDistiller.from_dict(_graph(nodes=nodes), max_nodes=5)

    def test_under_max_nodes_ok(self):
        nodes = [_node(f"n{i}") for i in range(3)]
        StructuralDistiller.from_dict(_graph(nodes=nodes), max_nodes=5).distill()

    def test_max_bytes_exceeded_raises(self, tmp_path):
        p = tmp_path / "big.json"
        p.write_text(json.dumps(_graph(nodes=[_node("n1")])))
        with pytest.raises(StructuralError):
            StructuralDistiller.from_path(p, max_bytes=10)  # file is > 10 bytes


# ── provenance / staleness ──────────────────────────────────────────────────────
class TestProvenance:
    def test_built_at_commit_passthrough(self):
        sig = StructuralDistiller.from_dict(_graph(commit="deadbeef")).distill()
        assert sig.built_at_commit == "deadbeef"

    def test_content_hash_deterministic(self):
        g = _graph(nodes=[_node("n1")])
        a = StructuralDistiller.from_dict(g).distill().content_hash
        b = StructuralDistiller.from_dict(g).distill().content_hash
        assert a == b and a

    def test_content_hash_changes_with_content(self):
        a = StructuralDistiller.from_dict(_graph(nodes=[_node("n1")])).distill()
        b = StructuralDistiller.from_dict(_graph(nodes=[_node("n2")])).distill()
        assert a.content_hash != b.content_hash

    def test_from_path_hash_is_raw_bytes(self, tmp_path):
        import hashlib
        g = _graph(nodes=[_node("n1")])
        p = tmp_path / "g.json"
        raw = json.dumps(g).encode("utf-8")
        p.write_bytes(raw)
        sig = StructuralDistiller.from_path(p).distill()
        assert sig.content_hash == hashlib.sha256(raw).hexdigest()[:16]


# ── R10: imported cognition is data, never code ──────────────────────────────────
class TestR10Safety:
    def test_code_looking_label_is_inert_string(self):
        evil = "__import__('os').system('echo PWNED')"
        g = _graph(nodes=[_node("n1", label=evil, community=0)])
        d = StructuralDistiller.from_dict(g)
        assert d.lookup("n1")["label"] == evil          # returned verbatim
        assert d.distill().communities[0].top_labels == (evil,)  # never executed

    def test_module_imports_no_third_party_or_code_exec(self):
        # R10: data, never code. Assert no networkx IMPORT (the word may appear in
        # the docstring explaining its absence) and no eval/exec/pickle.
        src = pathlib.Path(
            __file__).resolve().parent.parent / "conscio" / "structural.py"
        text = src.read_text()
        assert "import networkx" not in text and "from networkx" not in text
        assert "eval(" not in text and "exec(" not in text
        assert "import pickle" not in text and "pickle." not in text


# ── real trimmed fixture through from_path ───────────────────────────────────────
class TestFixture:
    def test_fixture_distills(self):
        sig = StructuralDistiller.from_path(FIXTURE).distill()
        assert isinstance(sig, StructuralSignal)
        assert len(sig.hyperedges) == 24
        assert sig.node_count == 101
        assert sig.built_at_commit.startswith("48f14a61")

    def test_fixture_communities_ranked(self):
        sig = StructuralDistiller.from_path(FIXTURE).distill()
        sizes = [c.size for c in sig.communities]
        assert sizes == sorted(sizes, reverse=True)
        assert sig.communities[0].size == 9  # community 33

    def test_fixture_lookup_real_hyperedge(self):
        d = StructuralDistiller.from_path(FIXTURE)
        hit = d.lookup("agency_act_cycle_pipeline")
        assert hit["kind"] == "hyperedge"
        assert "act_actpipeline_act" in hit["nodes"]

    def test_fixture_lookup_real_node(self):
        d = StructuralDistiller.from_path(FIXTURE)
        hit = d.lookup("conscio_engine_reflect")
        assert hit["kind"] == "node"
        assert hit["source_file"] == "conscio/engine.py"
        assert hit["community"] == 33

    def test_graphnode_dataclass_shape(self):
        # GraphNode is the typed projection used internally
        n = GraphNode(id="x", label="L", file_type="code",
                      source_file="f.py", source_location="L1", community=0)
        assert n.id == "x" and n.community == 0
