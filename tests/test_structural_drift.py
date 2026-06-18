# tests/test_structural_drift.py
"""v1.8.0 Structural Drift — temporal awareness over an ingested structure.

v1.7 made the agent INGEST a snapshot; v1.8 makes it NOTICE change. These tests
pin the pure data layer: the persisted digest, the prev→current delta, the
git-HEAD freshness read, and the fail-tolerant store. No subprocess is used or
allowed (R10 by spirit — pure data + stdlib only).
"""
import json
import pathlib


from conscio.structural import StructuralDistiller, StructuralSignal
from conscio.structural_drift import (
    DRIFT_FILENAME,
    StructuralDigest,
    StructuralDriftStore,
    compute_delta,
    compute_freshness,
    drift_path,
    read_head_commit,
)

SHA_A = "48f14a61aa36b2c1d0e9f8a7b6c5d4e3f2a1b0c9"
SHA_B = "0123456789abcdef0123456789abcdef01234567"


def _sig(nodes=None, hyperedges=None, links=None, commit="abc123") -> StructuralSignal:
    g = {"nodes": nodes or [], "hyperedges": hyperedges or [],
         "links": links or [], "built_at_commit": commit}
    return StructuralDistiller.from_dict(g).distill()


def _node(nid, label="n", community=0, source_file="a.py"):
    return {"id": nid, "label": label, "community": community,
            "source_file": source_file, "source_location": "L1", "file_type": "code"}


def _hyper(hid, label, nodes=("n1",)):
    return {"id": hid, "label": label, "nodes": list(nodes),
            "relation": "participate_in", "confidence_score": 0.9, "source_file": "a.py"}


def _digest(commit="c0", h="hash0", nodes=0, links=0, hyperedges=None, communities=None):
    return StructuralDigest(
        commit=commit, content_hash=h, node_count=nodes, link_count=links,
        hyperedges=hyperedges or {}, communities=communities or {}, seen_at="2026-01-01T00:00:00")


# ── StructuralDigest ──────────────────────────────────────────────────────────
class TestDigest:
    def test_from_signal_maps_fields(self):
        sig = _sig(
            nodes=[_node("n1", community=0), _node("n2", community=0),
                   _node("n3", community=1)],
            hyperedges=[_hyper("h1", "Edge One"), _hyper("h2", "Edge Two")],
            links=[{"source": "n1", "target": "n2"}],
            commit="deadbeef")
        d = StructuralDigest.from_signal(sig)
        assert d.commit == "deadbeef"
        assert d.content_hash == sig.content_hash
        assert d.node_count == 3
        assert d.link_count == 1
        assert d.hyperedges == {"h1": "Edge One", "h2": "Edge Two"}
        assert d.communities == {"0": 2, "1": 1}     # id(str) -> size
        assert isinstance(d.seen_at, str) and d.seen_at

    def test_json_round_trip(self):
        d = _digest(commit="c1", h="abcd", nodes=5, links=7,
                    hyperedges={"h1": "E1"}, communities={"0": 5})
        back = StructuralDigest.from_json(d.to_json())
        assert back == d

    def test_from_json_malformed_returns_none(self):
        assert StructuralDigest.from_json("not a dict") is None
        assert StructuralDigest.from_json({"commit": "c"}) is None        # missing keys
        assert StructuralDigest.from_json({}) is None

    def test_from_json_coerces_types(self):
        raw = {"commit": "c", "content_hash": "h", "node_count": "5",
               "link_count": "2", "hyperedges": {"h1": "E1"},
               "communities": {"0": "9"}, "seen_at": "t"}
        d = StructuralDigest.from_json(raw)
        assert d is not None
        assert d.node_count == 5 and d.communities == {"0": 9}


# ── compute_delta ─────────────────────────────────────────────────────────────
class TestComputeDelta:
    def test_first_sight_when_no_prior(self):
        d = compute_delta(None, _sig(commit="x"))
        assert d.first_sight is True
        assert d.changed is False
        assert d.commit_to == "x"

    def test_identical_is_not_changed(self):
        sig = _sig(nodes=[_node("n1")], hyperedges=[_hyper("h1", "E1")], commit="c")
        prev = StructuralDigest.from_signal(sig)
        d = compute_delta(prev, sig)
        assert d.first_sight is False
        assert d.changed is False
        assert d.summary == "structure unchanged"

    def test_commit_change_detected(self):
        prev = _digest(commit="old", h="H")
        sig = _sig(commit="new")
        d = compute_delta(prev, sig)
        assert d.commit_changed is True
        assert d.commit_from == "old" and d.commit_to == "new"
        assert d.changed is True

    def test_hash_change_alone_is_changed(self):
        # same commit, same topology, different content hash
        prev = _digest(commit="c", h="OLDHASH", nodes=1)
        sig = _sig(nodes=[_node("n1")], commit="c")
        d = compute_delta(prev, sig)
        assert d.commit_changed is False
        assert d.hash_changed is True
        assert d.changed is True

    def test_node_and_link_deltas(self):
        prev = _digest(commit="c", h=_sig(commit="c").content_hash, nodes=2, links=1)
        sig = _sig(nodes=[_node("n1"), _node("n2"), _node("n3"), _node("n4")],
                   links=[{"s": 1}, {"s": 2}, {"s": 3}], commit="c")
        d = compute_delta(prev, sig)
        assert d.node_delta == 2      # 4 - 2
        assert d.link_delta == 2      # 3 - 1

    def test_hyperedge_added_by_label(self):
        prev = _digest(hyperedges={"h1": "E1"})
        sig = _sig(hyperedges=[_hyper("h1", "E1"), _hyper("h2", "Edge Two")])
        d = compute_delta(prev, sig)
        assert d.hyperedges_added == ("Edge Two",)
        assert d.hyperedges_removed == ()

    def test_hyperedge_removed_by_label(self):
        prev = _digest(hyperedges={"h1": "E1", "h2": "Gone Edge"})
        sig = _sig(hyperedges=[_hyper("h1", "E1")])
        d = compute_delta(prev, sig)
        assert d.hyperedges_removed == ("Gone Edge",)
        assert d.hyperedges_added == ()

    def test_relabel_only_is_not_add_remove(self):
        # same hyperedge id, new label -> topology unchanged, NOT add/remove
        prev = _digest(hyperedges={"h1": "Old Label"})
        sig = _sig(hyperedges=[_hyper("h1", "New Label")])
        d = compute_delta(prev, sig)
        assert d.hyperedges_added == ()
        assert d.hyperedges_removed == ()

    def test_communities_added_removed_resized(self):
        prev = _digest(communities={"0": 5, "1": 3, "9": 2})
        sig = _sig(nodes=(
            [_node(f"a{i}", community=0) for i in range(5)]      # 0 unchanged: 5
            + [_node(f"b{i}", community=1) for i in range(7)]    # 1 resized: 3->7
            + [_node("c0", community=2)]))                        # 2 added; 9 removed
        d = compute_delta(prev, sig)
        assert d.communities_added == (2,)
        assert d.communities_removed == (9,)
        assert d.communities_resized == ((1, 3, 7),)
        assert d.changed is True

    def test_to_advisory_shape(self):
        prev = _digest(commit="old", hyperedges={"h1": "E1"})
        sig = _sig(hyperedges=[_hyper("h2", "E2")], commit="new")
        adv = compute_delta(prev, sig).to_advisory()
        assert adv["changed"] is True
        assert adv["commit_changed"] is True
        assert adv["hyperedges_added"] == ["E2"]
        assert adv["hyperedges_removed"] == ["E1"]
        assert isinstance(adv["summary"], str) and adv["summary"]


# ── StructuralDriftStore ──────────────────────────────────────────────────────
class TestDriftStore:
    def test_drift_path(self, tmp_path):
        assert drift_path(tmp_path) == tmp_path / DRIFT_FILENAME

    def test_get_none_when_empty(self, tmp_path):
        store = StructuralDriftStore(tmp_path / "drift.json")
        assert store.get("ws1") is None

    def test_put_then_get(self, tmp_path):
        store = StructuralDriftStore(tmp_path / "drift.json")
        d = _digest(commit="c1", hyperedges={"h1": "E1"}, communities={"0": 3})
        store.put("ws1", d)
        assert store.get("ws1") == d

    def test_persists_across_instances(self, tmp_path):
        p = tmp_path / "drift.json"
        StructuralDriftStore(p).put("ws1", _digest(commit="cX"))
        assert StructuralDriftStore(p).get("ws1").commit == "cX"

    def test_corrupt_store_is_empty(self, tmp_path):
        p = tmp_path / "drift.json"
        p.write_text("}{ not json")
        assert StructuralDriftStore(p).get("ws1") is None

    def test_non_dict_store_is_empty(self, tmp_path):
        p = tmp_path / "drift.json"
        p.write_text('["a list"]')
        assert StructuralDriftStore(p).get("ws1") is None

    def test_skips_malformed_entries(self, tmp_path):
        p = tmp_path / "drift.json"
        p.write_text(json.dumps({"ws1": {"junk": 1}, "ws2": _digest(commit="ok").to_json()}))
        store = StructuralDriftStore(p)
        assert store.get("ws1") is None
        assert store.get("ws2").commit == "ok"

    def test_save_failure_never_raises(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x")                      # a FILE where a dir is needed
        store = StructuralDriftStore(blocker / "sub" / "drift.json")
        store.put("ws1", _digest())                  # mkdir fails -> swallowed
        assert store.get("ws1") is not None          # still in memory


# ── read_head_commit (pure .git read) ─────────────────────────────────────────
def _mk_git(root, head, refs=None, packed=None, gitdir=None):
    if gitdir is not None:
        (root / ".git").write_text(f"gitdir: {gitdir}\n")
        g = pathlib.Path(gitdir)
    else:
        g = root / ".git"
    g.mkdir(parents=True, exist_ok=True)
    (g / "HEAD").write_text(head)
    for ref, sha in (refs or {}).items():
        rp = g / ref
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(sha)
    if packed is not None:
        (g / "packed-refs").write_text(packed)


class TestReadHeadCommit:
    def test_ref_with_loose_ref(self, tmp_path):
        _mk_git(tmp_path, "ref: refs/heads/main\n", refs={"refs/heads/main": SHA_A + "\n"})
        assert read_head_commit(tmp_path) == SHA_A

    def test_detached_head(self, tmp_path):
        _mk_git(tmp_path, SHA_A + "\n")
        assert read_head_commit(tmp_path) == SHA_A

    def test_packed_refs_fallback(self, tmp_path):
        packed = f"# pack-refs with: peeled fully-peeled sorted\n{SHA_A} refs/heads/main\n^{SHA_B}\n"
        _mk_git(tmp_path, "ref: refs/heads/main\n", packed=packed)
        assert read_head_commit(tmp_path) == SHA_A

    def test_git_file_worktree(self, tmp_path):
        real = tmp_path / "real_gitdir"
        _mk_git(tmp_path, SHA_B + "\n", gitdir=str(real))
        assert read_head_commit(tmp_path) == SHA_B

    def test_missing_git_returns_none(self, tmp_path):
        assert read_head_commit(tmp_path) is None

    def test_malformed_head_returns_none(self, tmp_path):
        _mk_git(tmp_path, "this is not a sha or a ref\n")
        assert read_head_commit(tmp_path) is None

    def test_ref_points_at_missing_returns_none(self, tmp_path):
        _mk_git(tmp_path, "ref: refs/heads/ghost\n")     # no loose, no packed
        assert read_head_commit(tmp_path) is None


# ── compute_freshness ─────────────────────────────────────────────────────────
class TestComputeFreshness:
    def test_up_to_date(self, tmp_path):
        _mk_git(tmp_path, SHA_A + "\n")
        f = compute_freshness(tmp_path, SHA_A)
        assert f.known is True
        assert f.is_stale is False

    def test_stale_when_head_differs(self, tmp_path):
        _mk_git(tmp_path, SHA_B + "\n")
        f = compute_freshness(tmp_path, SHA_A)
        assert f.known is True
        assert f.is_stale is True
        assert f.head_commit == SHA_B and f.graph_commit == SHA_A

    def test_unknown_when_no_git(self, tmp_path):
        f = compute_freshness(tmp_path, SHA_A)
        assert f.head_commit is None
        assert f.known is False
        assert f.is_stale is False

    def test_unknown_when_graph_commit_empty(self, tmp_path):
        _mk_git(tmp_path, SHA_A + "\n")
        f = compute_freshness(tmp_path, "")
        assert f.known is False
        assert f.is_stale is False

    def test_to_advisory_shape(self, tmp_path):
        _mk_git(tmp_path, SHA_B + "\n")
        adv = compute_freshness(tmp_path, SHA_A).to_advisory()
        assert adv["stale"] is True
        assert adv["head_commit"] == SHA_B
        assert adv["graph_commit"] == SHA_A


# ── no subprocess (R10 by spirit) ─────────────────────────────────────────────
def test_module_uses_no_subprocess_or_shell():
    src = pathlib.Path(__file__).resolve().parent.parent / "conscio" / "structural_drift.py"
    text = src.read_text()
    assert "import subprocess" not in text and "subprocess." not in text
    assert "import os" not in text or "os.system" not in text
    assert "popen" not in text.lower()
