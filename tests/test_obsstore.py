"""ObsStore — full-fidelity, content-addressed observation storage."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from conscio import obsstore


@pytest.fixture()
def conn(tmp_path):
    c = obsstore.connect(tmp_path / "obs.db")
    yield c
    c.close()


def test_blob_round_trips_a_large_payload_byte_for_byte(conn):
    raw = b"line 0\n" * 20000  # ~140 KB, far past the old cap
    h, n, clipped = obsstore.put_blob(conn, raw)
    assert clipped is False
    assert n == len(raw)
    assert obsstore.get_blob(conn, h) == raw


def test_identical_payloads_share_one_blob(conn):
    raw = b"the same bytes exactly"
    h1, _, _ = obsstore.put_blob(conn, raw)
    h2, _, _ = obsstore.put_blob(conn, raw)
    assert h1 == h2
    assert conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 1


def test_oversized_payload_is_clipped_and_flagged(conn):
    raw = b"x" * (obsstore.MAX_FIELD_BYTES + 5000)
    h, n, clipped = obsstore.put_blob(conn, raw)
    assert clipped is True
    assert n == obsstore.MAX_FIELD_BYTES
    assert len(obsstore.get_blob(conn, h)) == obsstore.MAX_FIELD_BYTES


def test_schema_version_is_stamped(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == obsstore.SCHEMA_VERSION


def _obs(conn, **kw):
    base = {"tool": "Bash", "input_text": "cmd", "output_text": "out",
            "project": "p", "agent": "claude-code", "session_id": "S1",
            "ts": "2026-07-31T00:00:00"}
    base.update(kw)
    return obsstore.put_observation(conn, **base)


def test_observation_stores_full_output_not_a_1024_char_prefix(conn):
    big = "needle-at-the-very-end " * 500 + "FINALTOKEN"
    _obs(conn, output_text=big)
    got = obsstore.search(conn, "FINALTOKEN", session_id="S1", full=True)
    assert got and got[0]["output"] == big


def test_search_defaults_to_the_current_session_only(conn):
    _obs(conn, session_id="S1", output_text="secret ALPHA")
    _obs(conn, session_id="S2", output_text="secret BETA")
    got = obsstore.search(conn, "secret", session_id="S2", full=True)
    assert [r["output"] for r in got] == ["secret BETA"]


def test_search_can_widen_to_project_then_to_all(conn):
    _obs(conn, session_id="S1", project="p", output_text="secret ALPHA")
    _obs(conn, session_id="S2", project="p", output_text="secret BETA")
    _obs(conn, session_id="S3", project="other", output_text="secret GAMMA")
    proj = obsstore.search(conn, "secret", scope="project", project="p",
                           session_id="S2", k=10, full=True)
    assert sorted(r["output"] for r in proj) == ["secret ALPHA", "secret BETA"]
    every = obsstore.search(conn, "secret", scope="all", k=10, full=True)
    assert len(every) == 3


def test_search_returns_session_id_so_callers_can_filter(conn):
    _obs(conn, session_id="S9", output_text="marker")
    assert obsstore.search(conn, "marker", session_id="S9")[0]["session_id"] == "S9"


def test_snippet_mode_returns_a_window_not_the_whole_row(conn):
    big = "pad " * 4000 + "NEEDLE " + "pad " * 4000
    _obs(conn, output_text=big)
    win = obsstore.search(conn, "NEEDLE", session_id="S1")[0]["output"]
    assert "NEEDLE" in win
    assert len(win) < len(big) / 10


def test_fts_query_operators_are_defused(conn):
    _obs(conn, output_text="alpha beta")
    assert obsstore.search(conn, 'alpha OR "', session_id="S1") == []


def test_k_is_floored_so_a_zero_never_means_unlimited(conn):
    for i in range(5):
        _obs(conn, output_text=f"row {i} marker")
    assert len(obsstore.search(conn, "marker", session_id="S1", k=0)) == 1


def test_an_unknown_scope_is_a_programming_error_not_a_silent_widening(conn):
    _obs(conn, output_text="marker")
    with pytest.raises(ValueError):
        obsstore.search(conn, "marker", scope="everything", session_id="S1")


def test_prune_drops_old_rows_and_then_orphan_blobs(conn):
    _obs(conn, output_text="ancient", ts="2020-01-01T00:00:00")
    _obs(conn, output_text="recent", ts="2099-01-01T00:00:00")
    before = conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0]
    stats = obsstore.prune(conn, max_age_days=30)
    assert stats["observations_deleted"] == 1
    assert stats["blobs_deleted"] >= 1
    assert conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] < before
    assert obsstore.search(conn, "recent", session_id="S1", full=True)
    assert obsstore.search(conn, "ancient", session_id="S1") == []


def test_prune_keeps_a_blob_still_referenced_by_a_survivor(conn):
    shared = "identical payload"
    _obs(conn, output_text=shared, ts="2020-01-01T00:00:00")
    _obs(conn, output_text=shared, ts="2099-01-01T00:00:00")
    obsstore.prune(conn, max_age_days=30)
    got = obsstore.search(conn, "identical", session_id="S1", full=True)
    assert got and got[0]["output"] == shared


def test_prune_actually_brings_the_store_under_the_byte_cap(conn):
    """The cap is a guarantee, not a suggestion: one pass must land under it."""
    for i in range(20):
        _obs(conn, output_text=os.urandom(4096).hex(),  # incompressible
             ts=f"2099-01-{i + 1:02d}T00:00:00")
    assert obsstore.stored_bytes(conn) > 60_000
    stats = obsstore.prune(conn, max_age_days=36500, max_bytes=60_000)
    assert obsstore.stored_bytes(conn) <= 60_000
    assert stats["observations_deleted"] > 0


def test_prune_evicts_oldest_first_so_the_newest_survives(conn):
    for i in range(10):
        _obs(conn, output_text=f"ROW{i:02d} " + os.urandom(2048).hex(),
             ts=f"2099-01-{i + 1:02d}T00:00:00")
    obsstore.prune(conn, max_age_days=36500, max_bytes=8_000)
    left = conn.execute("SELECT ts FROM observations ORDER BY ts").fetchall()
    assert left, "the cap must not empty a store it can satisfy"
    assert left[-1][0] == "2099-01-10T00:00:00"


def test_prune_on_an_empty_store_is_a_no_op(conn):
    assert obsstore.prune(conn) == {"observations_deleted": 0, "blobs_deleted": 0}


def test_prune_with_an_unreachable_cap_empties_rather_than_spinning(conn):
    _obs(conn, output_text="x" * 5000, ts="2099-01-01T00:00:00")
    obsstore.prune(conn, max_age_days=36500, max_bytes=1)
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def _make_v1(path):
    """Build a database in the exact v3.8.2 shape, with truncated payloads."""
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE observations(id INTEGER PRIMARY KEY AUTOINCREMENT,"
              " tool TEXT, input TEXT, output TEXT, project TEXT, agent TEXT,"
              " session_id TEXT, ts TEXT)")
    c.execute("CREATE VIRTUAL TABLE obs_fts USING fts5(tool, input, output)")
    cur = c.execute("INSERT INTO observations(tool, input, output, project, agent,"
                    " session_id, ts) VALUES(?,?,?,?,?,?,?)",
                    ("Bash", "old-in", "old-out-legacy", "p", "hermes",
                     "OLD", "2026-07-30T00:00:00"))
    c.execute("INSERT INTO obs_fts(rowid, tool, input, output) VALUES(?,?,?,?)",
              (cur.lastrowid, "Bash", "old-in", "old-out-legacy"))
    c.commit()
    c.close()


def test_v1_database_migrates_and_stays_searchable(tmp_path):
    p = tmp_path / "obs.db"
    _make_v1(p)
    c = obsstore.connect(p)
    assert c.execute("PRAGMA user_version").fetchone()[0] == obsstore.SCHEMA_VERSION
    got = obsstore.search(c, "legacy", session_id="OLD", full=True)
    assert got[0]["output"] == "old-out-legacy"
    assert got[0]["truncated"] is True  # v1 payloads were capped at 1024
    c.close()


def test_migration_is_idempotent(tmp_path):
    p = tmp_path / "obs.db"
    _make_v1(p)
    obsstore.connect(p).close()
    c = obsstore.connect(p)
    assert c.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    c.close()


def test_migration_leaves_no_stale_fts_rows(tmp_path):
    """The v1 index must be rebuilt, not appended to — one row, one hit."""
    p = tmp_path / "obs.db"
    _make_v1(p)
    c = obsstore.connect(p)
    assert len(obsstore.search(c, "legacy", scope="all", k=10)) == 1
    c.close()


def test_concurrent_first_open_of_a_v1_file_migrates_exactly_once(tmp_path):
    """Concurrent first opens must converge on one migration, never double-run.

    Whether any given run hits the pre-lock/post-lock window is timing-dependent,
    so this asserts the outcome — exact row count, no leftover v1 table, still
    searchable — rather than claiming to force the race every time.
    """
    p = tmp_path / "obs.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE observations(id INTEGER PRIMARY KEY AUTOINCREMENT,"
              " tool TEXT, input TEXT, output TEXT, project TEXT, agent TEXT,"
              " session_id TEXT, ts TEXT)")
    c.execute("CREATE VIRTUAL TABLE obs_fts USING fts5(tool, input, output)")
    for i in range(50):
        cur = c.execute("INSERT INTO observations(tool, input, output, project,"
                        " agent, session_id, ts) VALUES(?,?,?,?,?,?,?)",
                        ("Bash", f"in{i}", f"RACEMARK{i}", "p", "a", "R",
                         "2026-07-30T00:00:00"))
        c.execute("INSERT INTO obs_fts(rowid, tool, input, output) VALUES(?,?,?,?)",
                  (cur.lastrowid, "Bash", f"in{i}", f"RACEMARK{i}"))
    c.commit()
    c.close()

    mod_path = Path(obsstore.__file__)
    script = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('obsstore', r'{mod_path}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        f"c = m.connect(r'{p}')\n"
        "print(c.execute('SELECT COUNT(*) FROM observations').fetchone()[0])\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(4)]
    outs = [p_.communicate() for p_ in procs]
    for (out, err), p_ in zip(outs, procs):
        assert p_.returncode == 0, err
        assert out.strip() == "50", f"row count drifted: {out!r}"

    c = obsstore.connect(p)
    assert c.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 50
    assert not c.execute(
        "SELECT name FROM sqlite_master WHERE name='observations_v1'").fetchall()
    assert len(obsstore.search(c, "RACEMARK7", session_id="R")) == 1
    c.close()


def test_a_payload_cut_mid_codepoint_still_round_trips(conn):
    """The cap is a byte boundary; a 4-byte emoji straddling it must not raise."""
    head = "a" * (obsstore.MAX_FIELD_BYTES - 2)
    oid = obsstore.put_observation(
        conn, tool="Bash", input_text="i", output_text=head + "🌍 tail",
        project="p", agent="a", session_id="S1", ts="t")
    got = obsstore.read_observation(conn, oid)
    assert got["truncated"] is True
    assert got["output"].startswith("a" * 100)
    # whatever survived the cut, the blob and the index agree on it
    fts = conn.execute("SELECT output FROM obs_fts WHERE rowid=?", (oid,)).fetchone()[0]
    assert fts == got["output"]


def test_module_loads_without_importing_the_conscio_package(tmp_path):
    """The v3.9 hook loads this file directly; conscio must stay out of sys.modules."""
    mod_path = Path(obsstore.__file__)
    db_path = tmp_path / "standalone.db"
    script = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('obsstore', r'{mod_path}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        f"c = m.connect(r'{db_path}')\n"
        "m.put_observation(c, tool='Bash', input_text='i', output_text='marker',\n"
        "                  project='p', agent='a', session_id='S', ts='t')\n"
        "assert m.search(c, 'marker', session_id='S')\n"
        "assert 'conscio' not in sys.modules, sorted(sys.modules)[:5]\n"
        "print('OK')\n"
    )
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_engine_stores_full_output_and_scopes_recall(tmp_path):
    from conscio import ConsciousnessEngine

    e = ConsciousnessEngine(model_name="t", storage_path=str(tmp_path))
    try:
        big = "x " * 5000 + "TAILMARKER"
        e.set_session("SESSION-AAA")
        e.observe("Bash", "git status", "ALPHA-ONLY", project="alpha")
        e.set_session("SESSION-BBB")
        e.observe("Bash", "git status", big, project="beta")

        # the 1024-char cap is gone
        got = e.recall_observations("TAILMARKER", full=True)
        assert got and got[0]["output"] == big

        # and a recall in session BBB no longer sees session AAA
        assert e.recall_observations("ALPHA-ONLY") == []
        assert e.recall_observations("ALPHA-ONLY", scope="all")[0]["project"] == "alpha"

        assert "session_id" in got[0]
    finally:
        e.close()


def test_engine_compress_observations_survives_the_v2_schema(tmp_path):
    """The handoff path reads obs.db directly; v2 dropped the columns it used."""
    from conscio import ConsciousnessEngine

    e = ConsciousnessEngine(model_name="t", storage_path=str(tmp_path))
    try:
        e.set_session("SESSION-H")
        e.observe("Bash", "git status", "on branch main", project="p")
        e.observe("Read", "engine.py", "y " * 200_000 + "TAIL", project="p")
        got = e.compress_observations()
        assert got["count"] == 2
        assert "git status" in got["handoff"]
        assert len(got["handoff"]) < 10_000, "a whole payload leaked into the handoff"
    finally:
        e.close()


def test_engine_project_scope_needs_the_project_it_observed_with(tmp_path):
    from conscio import ConsciousnessEngine

    e = ConsciousnessEngine(model_name="t", storage_path=str(tmp_path))
    try:
        e.set_session("SESSION-P")
        e.observe("Bash", "cmd", "PROJMARKER", project="alpha")
        assert e.recall_observations("PROJMARKER", scope="project",
                                     project="alpha")[0]["project"] == "alpha"
        # omitting it must be loud, not an empty result that reads like a miss
        with pytest.raises(ValueError):
            e.recall_observations("PROJMARKER", scope="project")
    finally:
        e.close()


def test_engine_observe_still_never_raises(tmp_path):
    """Telemetry must not break the caller even when the store is unusable."""
    from conscio import ConsciousnessEngine

    e = ConsciousnessEngine(model_name="t", storage_path=str(tmp_path))
    try:
        e._obs_conn().close()  # simulate a dead handle mid-session
        assert e.observe("Bash", "cmd", "out") == -1
    finally:
        e.close()
