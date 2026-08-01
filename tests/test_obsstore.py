"""ObsStore — full-fidelity, content-addressed observation storage."""
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
    raw = ("line %d\n" % 0).encode() * 20000  # ~140 KB, far past the old cap
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
    base = dict(tool="Bash", input_text="cmd", output_text="out",
                project="p", agent="claude-code", session_id="S1",
                ts="2026-07-31T00:00:00")
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


def test_module_loads_without_importing_the_conscio_package(tmp_path):
    """The v3.9 hook loads this file directly; conscio must stay out of sys.modules."""
    mod_path = Path(obsstore.__file__)
    script = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('obsstore', r'{mod_path}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        f"c = m.connect(r'{tmp_path / "standalone.db"}')\n"
        "m.put_observation(c, tool='Bash', input_text='i', output_text='marker',\n"
        "                  project='p', agent='a', session_id='S', ts='t')\n"
        "assert m.search(c, 'marker', session_id='S')\n"
        "assert 'conscio' not in sys.modules, sorted(sys.modules)[:5]\n"
        "print('OK')\n"
    )
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
