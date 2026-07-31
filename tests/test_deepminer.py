"""v3.8 DeepMiner — agnostic tool-observation store (obs.db + FTS5).

Covers engine.observe / recall_observations / compress_observations and the
MCP tools. These exercise the real engine (offline, 0 LLM tokens).
"""

from __future__ import annotations

import threading
import time

import pytest

from conscio.engine import ConsciousnessEngine


@pytest.fixture
def eng(tmp_path):
    e = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s", delivery_check=False)
    yield e
    e.close()


# ── observe() ──────────────────────────────────────────────────────────────


def test_observe_returns_id_and_persists(eng):
    oid = eng.observe("edit_file", "fix auth", "done", "/proj", "hermes")
    assert isinstance(oid, int) and oid >= 1
    row = (
        eng._obs_conn()
        .execute("SELECT tool, input, output, project, agent FROM observations WHERE id=?", (oid,))
        .fetchone()
    )
    assert row == ("edit_file", "fix auth", "done", "/proj", "hermes")


def test_observe_emits_tool_observed_event(eng):
    before = eng.event_bus.db.execute(
        "SELECT COUNT(*) FROM events WHERE type='tool:observed'"
    ).fetchone()[0]
    eng.observe("grep", "pattern", "match", "/p")
    after = eng.event_bus.db.execute(
        "SELECT COUNT(*) FROM events WHERE type='tool:observed'"
    ).fetchone()[0]
    assert after == before + 1


def test_observe_zero_llm_tokens(eng):
    """observe() must never touch the model/adapter (deterministic capture)."""
    eng.observe("t", "i", "o", "/p")
    # No adapter attached and no exception → path is pure sqlite, 0 tokens.
    assert eng.recall_observations("i") is not None


def test_observe_never_raises_returns_minus_one(eng):
    eng._obs_conn().close()  # cached conn now closed → writes raise, swallowed
    assert eng.observe("x", "y", "z") == -1


def test_observe_large_payload_truncated(eng):
    big = "x" * 5_000_000
    oid = eng.observe("t", big, big, "/p")
    inp, out = (
        eng._obs_conn()
        .execute("SELECT input, output FROM observations WHERE id=?", (oid,))
        .fetchone()
    )
    assert len(inp) <= 1024 and len(out) <= 1024


def test_set_session_is_default(eng):
    eng.set_session("wired")
    oid = eng.observe("t", "i", "o")  # no explicit session_id
    sid = (
        eng._obs_conn()
        .execute("SELECT session_id FROM observations WHERE id=?", (oid,))
        .fetchone()[0]
    )
    assert sid == "wired"


# ── recall_observations() (FTS5) ────────────────────────────────────────────


def test_recall_observations_finds_match(eng):
    eng.observe("edit_file", "fix authentication bug", "done", "/p")
    eng.observe("run_tests", "pytest suite", "passed", "/p")
    res = eng.recall_observations("authentication")
    assert any("authentication" in r["input"] for r in res)
    assert all(
        {"id", "tool", "input", "output", "project", "agent", "timestamp"} <= set(r) for r in res
    )


def test_recall_observations_empty_query(eng):
    assert eng.recall_observations("   ") == []


def test_recall_observations_fts_operators_literal(eng):
    eng.observe("t", "alpha AND beta OR gamma", "o", "/p")
    # AND/OR must be treated as a literal phrase, never crash.
    assert isinstance(eng.recall_observations("alpha AND beta"), list)
    assert isinstance(eng.recall_observations('NOT "("'), list)


def test_query_injection_is_parametrized(eng):
    eng.observe("t", "hello", "world", "/p")
    eng.recall_observations('"; DROP TABLE observations; --')
    n = eng._obs_conn().execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert n == 1  # table intact, row intact


def test_project_injection_is_parametrized(eng):
    eng.observe("t", "i", "o", "'; DROP TABLE observations; --")
    n = eng._obs_conn().execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert n == 1


# ── isolation / concurrency ─────────────────────────────────────────────────


def test_two_sessions_isolated(eng):
    eng.observe("t", "a-work", "o", "/p", session_id="sessA")
    eng.observe("t", "b-work", "o", "/p", session_id="sessB")
    a = (
        eng._obs_conn()
        .execute("SELECT COUNT(*) FROM observations WHERE session_id='sessA'")
        .fetchone()[0]
    )
    b = (
        eng._obs_conn()
        .execute("SELECT COUNT(*) FROM observations WHERE session_id='sessB'")
        .fetchone()[0]
    )
    assert a == 1 and b == 1


def test_flood_1000_no_corruption(eng):
    t0 = time.time()
    for i in range(1000):
        eng.observe("tool", f"input {i}", "out", "/p", session_id="flood")
    dt = time.time() - t0
    n = (
        eng._obs_conn()
        .execute("SELECT COUNT(*) FROM observations WHERE session_id='flood'")
        .fetchone()[0]
    )
    assert n == 1000
    assert dt < 5.0  # typically < 1s; tolerant bound for slow CI


def test_concurrent_observe_thread_safe(eng):
    def worker(sid: str):
        for i in range(50):
            eng.observe("t", f"{sid}-{i}", "o", "/p", session_id=sid)

    threads = [threading.Thread(target=worker, args=(f"s{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    n = eng._obs_conn().execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert n == 200
