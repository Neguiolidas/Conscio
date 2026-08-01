"""v3.8.1 DeepMiner — intensive suite (confirm the big update end-to-end).

Complements ``test_deepminer.py`` (behaviour) and ``test_deepminer_hostile.py``
(destructive invariants). This file targets the gaps those two do **not** cover
— above all the *positive persistence roundtrip*: earlier suites only asserted
that ``compress_observations`` never writes ``_session_handoff.md`` (the
negative), never that the handoff is actually persisted and retrievable. Each
test exercises the real ``ConsciousnessEngine`` (offline, 0 LLM tokens).

  * Persistence roundtrip: compress → ``content_store`` → searchable, under a
    ``handoff_deepminer_*`` label in the shared ``conscio.db``.
  * Store isolation: raw ``obs.db`` and the ``conscio.db`` content store are
    distinct files whose contents never bleed into each other's queries.
  * Session precedence: explicit ``session_id=`` vs ``set_session`` default.
  * Truncation boundary, recall ``k`` bounds, verbatim SQL-meta storage,
    FTS metacharacter safety, and observe/compress concurrency.
"""

from __future__ import annotations

import glob
import threading

import pytest

from conscio import obsstore
from conscio.engine import ConsciousnessEngine
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings


@pytest.fixture
def eng(tmp_path):
    e = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s", delivery_check=False)
    yield e
    e.close()


def _bindings(eng):
    return Bindings(eng, SeenStore(":memory:"))


def _obs_db(eng) -> str:
    hits = glob.glob(str(eng.storage / "**" / "obs.db"), recursive=True)
    assert hits, "obs.db was never created"
    return hits[0]


# ── persistence roundtrip (the headline gap) ────────────────────────────────


def test_handoff_persisted_and_retrievable(eng):
    """compress() must durably persist the handoff AND make it searchable."""
    eng.observe("edit_file", "fix auth bug in login", "patched", "/p", session_id="s")
    eng.observe("run_tests", "pytest -q", "28 passed", "/p", session_id="s")
    res = eng.compress_observations("s")
    assert res["count"] == 2 and res["handoff"]

    found = eng.content_store.search("auth")
    assert found, "handoff not retrievable from content_store after compress"
    hit = found[0]
    assert hit.title.startswith("handoff_deepminer_")
    assert hit.source_category == "session"
    assert hit.session_id == "s"


def test_handoff_content_includes_observed_tools(eng):
    eng.observe("grep", "find needle", "hay", "/p", session_id="s")
    res = eng.compress_observations("s")
    assert "grep" in res["handoff"]
    # the persisted copy carries the same content, not just the returned string
    assert any("grep" in r.content for r in eng.content_store.search("needle"))


def test_compress_rerunnable_same_session(eng):
    """Re-running compress in the same wall-clock second must not raise
    (labels are second-granular — a collision must be tolerated)."""
    eng.observe("grep", "q", "o", "/p", session_id="s")
    a = eng.compress_observations("s")
    b = eng.compress_observations("s")
    c = eng.compress_observations("s")
    assert a["count"] == b["count"] == c["count"] == 1
    assert eng.content_store.search("q"), "handoff missing after repeated compress"


# ── store isolation ─────────────────────────────────────────────────────────


def test_obs_db_and_conscio_db_are_distinct_files(eng):
    eng.observe("grep", "x", "y", "/p", session_id="s")
    eng.compress_observations("s")
    obs = _obs_db(eng)
    con = str(eng.content_store.db_path)
    assert obs != con
    assert obs.endswith("obs.db") and con.endswith("conscio.db")


def test_compress_preserves_raw_observations(eng):
    """compress reads obs.db; it must not consume/wipe the raw rows."""
    eng.observe("grep", "keepme", "o", "/p", session_id="s")
    assert eng.compress_observations("s")["count"] == 1
    # raw observation still recallable afterwards
    assert eng.recall_observations("keepme", k=5, session_id="s")
    n = eng._obs_conn().execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert n == 1


def test_stores_do_not_bleed(eng):
    """recall_observations queries obs.db only; the handoff lives in conscio.db.

    A term that appears solely in the handoff header ('deepminer') must be
    absent from obs.db yet present in the content store.
    """
    eng.observe("grep", "needle", "hay", "/p", session_id="s")
    eng.compress_observations("s")
    assert eng.recall_observations("deepminer", k=5) == []
    assert eng.content_store.search("needle")


# ── session precedence ──────────────────────────────────────────────────────


def test_explicit_session_overrides_set_session(eng):
    eng.set_session("A")
    eng.observe("t", "i", "o", session_id="B")  # explicit wins over default
    assert eng.compress_observations("B")["count"] == 1
    assert eng.compress_observations("A")["count"] == 0


def test_compress_defaults_to_set_session(eng):
    eng.set_session("X")
    eng.observe("t", "i", "o")  # no explicit session_id → uses 'X'
    res = eng.compress_observations()  # no arg → uses 'X'
    assert res["session_id"] == "X" and res["count"] == 1


# ── boundary / bounds ───────────────────────────────────────────────────────


def test_truncation_boundary(eng):
    """v3.9 stores payloads whole; the old 1024-char cliff is gone."""
    for size in (1023, 1024, 2000):
        oid = eng.observe("t", "x" * size, "y" * size, "/p", session_id="s")
        got = obsstore.read_observation(eng._obs_conn(), oid)
        assert len(got["input"]) == size, (size, len(got["input"]))
        assert len(got["output"]) == size, (size, len(got["output"]))
        assert got["truncated"] is False


def test_recall_k_floor_defuses_negative_limit(eng):
    """k is floored via ``max(1, k)`` — this deliberately neutralizes SQLite's
    'LIMIT -1 means unlimited' footgun, so k<=0 can never dump every row."""
    for i in range(5):
        eng.observe("grep", f"needle {i}", "o", "/p", session_id="s")
    assert len(eng.recall_observations("needle", k=2, session_id="s")) == 2
    assert len(eng.recall_observations("needle", k=1000, session_id="s")) == 5
    # the guard: k=0 and k=-1 floor to 1 — NOT 0, and crucially NOT unlimited
    assert len(eng.recall_observations("needle", k=0, session_id="s")) == 1
    assert len(eng.recall_observations("needle", k=-1, session_id="s")) == 1


# ── verbatim storage + FTS metacharacter safety ─────────────────────────────


def test_observe_sql_meta_stored_verbatim_table_intact(eng):
    payload = "'); DROP TABLE observations;--"
    oid = eng.observe("t", payload, "sentinel_marker", "/p", session_id="s")
    got = obsstore.read_observation(eng._obs_conn(), oid)
    assert got["input"] == payload  # stored byte-for-byte, never executed
    assert eng._obs_conn().execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    assert eng.recall_observations("sentinel_marker", k=5, session_id="s")


def test_recall_fts_metacharacters_no_crash(eng):
    eng.observe("grep", "ordinary content", "o", "/p", session_id="s")
    for q in ('*', '"', "NEAR(a b)", "col:val", "^anchor", "a AND b", "( ) -", "🔥💥"):
        r = eng.recall_observations(q, k=5)
        assert isinstance(r, list), q
    # obs.db survived every metacharacter query
    assert eng._obs_conn().execute("PRAGMA integrity_check").fetchone()[0] == "ok"


# ── concurrency: observe under a compressing main thread ─────────────────────


def test_concurrent_observe_during_compress(eng):
    n_threads, per = 6, 40

    def worker(sid: str):
        for i in range(per):
            eng.observe("grep", f"c{i}", "o", "/p", session_id=sid)

    threads = [
        threading.Thread(target=worker, args=(f"s{t}",)) for t in range(n_threads)
    ]
    for t in threads:
        t.start()
    # hammer compress on a live session while writers run
    for _ in range(15):
        eng.compress_observations("s0")
    for t in threads:
        t.join()

    total = eng._obs_conn().execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert total == n_threads * per
    assert eng._obs_conn().execute("PRAGMA integrity_check").fetchone()[0] == "ok"


# ── MCP dispatch depth ──────────────────────────────────────────────────────


def test_mcp_roundtrip_ids_increment(eng):
    import json

    b = _bindings(eng)
    ids = []
    for k in range(3):
        res = b.call_tool(
            "conscio.observe", {"tool": "grep", "input": f"n{k}", "output": "o"}
        )
        ids.append(json.loads(res["content"][0]["text"])["observation_id"])
    assert ids == sorted(set(ids)) and len(set(ids)) == 3  # strictly increasing, unique
    res = b.call_tool("conscio.recall_observations", {"query": "n1"})
    assert json.loads(res["content"][0]["text"])["observations"]


def test_mcp_recall_empty_query_safe(eng):
    import json

    b = _bindings(eng)
    b.call_tool("conscio.observe", {"tool": "grep", "input": "x", "output": "y"})
    res = b.call_tool("conscio.recall_observations", {"query": ""})
    payload = json.loads(res["content"][0]["text"])
    assert isinstance(payload["observations"], list)  # empty or not, never a crash
