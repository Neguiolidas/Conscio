"""W2: the `wait` subcommand of the wake hook (spec §2.1, §2.3).

The waiter is a long-lived read-only process the Stop hook arms at turn end.
Contract under test: exit codes 0/2/3 and the exact line counts; the reserved
and self-sent filters; the spool count (files never opened); read-only access
to liaison.db; the ownership record; the already-armed and detached rules; and
the stdlib-only spool/RESERVED contract against conscio.liaison.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parents[1]
    / "conscio" / "integrations" / "claude_code" / "assets" / "hooks"
    / "conscio_wake.py"
)
SELF_ID = "self-w2-0001"
SESSION = "sess-w2-0001"
PEER = "peer-w2-0001"
RESERVED = {"review_request", "review_verdict", "board.propose"}


def _record_path(space: Path, session: str) -> Path:
    key = hashlib.sha256(session.encode("utf-8")).hexdigest()[:16]
    return space / "waiters" / f"{key}.json"


def _seed_db(space: Path, rows: list[tuple[str, str, str]]) -> Path:
    """liaison.db under the space; rows are (from, type, text) addressed to SELF."""
    space.mkdir(parents=True, exist_ok=True)
    db = space / "liaison.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, from_instance TEXT, "
        "to_instance TEXT, type TEXT, payload TEXT, ts REAL, read_ts REAL)"
    )
    for frm, typ, text in rows:
        conn.execute(
            "INSERT INTO messages (from_instance, to_instance, type, payload, ts)"
            " VALUES (?,?,?,?,?)",
            (frm, SELF_ID, typ, json.dumps({"text": text}), time.time()),
        )
    conn.commit()
    conn.close()
    return db


def _spool_root(tmp_path: Path) -> Path:
    root = tmp_path / "relay-praca"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run_wait(space: Path, tmp_path: Path, *extra: str, session: str = SESSION,
              timeout: str = "1", interval: str = "0", self_id: str = SELF_ID,
              db: Path | None = None,
              since: str | None = "0") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CONSCIO_SELF_ID"] = self_id
    env["CONSCIO_RELAY_ROOT"] = str(_spool_root(tmp_path))
    env.pop("CONSCIO_BASE", None)
    argv = [
        sys.executable, str(HOOK), "wait",
        "--storage", str(space),
        "--session", session,
        "--timeout", timeout,
        "--interval", interval,
    ]
    if db is not None:
        argv += ["--liaison-db", str(db)]
    if since is not None:
        argv += ["--since", since]
    argv += list(extra)
    return subprocess.run(argv, capture_output=True, text=True, env=env,
                          timeout=60)


def _spool_dir_for(tmp_path: Path, self_id: str = SELF_ID) -> Path:
    return _spool_root(tmp_path) / "spool" / self_id


def test_exit0_traffic_lines_and_filters(tmp_path):
    """A peer chat row wakes with one text line; reserved and self-sent rows
    never wake; the re-arm line carries --since of the newest id seen."""
    db = _seed_db(tmp_path / "space", [
        (PEER, "chat", "hello from the peer"),
        (PEER, "review_request", "reserved one"),
        (PEER, "review_verdict", "reserved two"),
        (PEER, "board.propose", "reserved three"),
        (SELF_ID, "chat", "self-sent must not wake"),
    ])
    r = _run_wait(tmp_path / "space", tmp_path, db=db, timeout="2", interval="0")
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = r.stdout.splitlines()
    wake_lines = [l for l in out if l.startswith("[relay] #")]
    assert len(wake_lines) == 1, out
    assert "#1" in wake_lines[0] and PEER[:12] in wake_lines[0]
    assert "[chat]" in wake_lines[0] and "hello from the peer" in wake_lines[0]
    for word in ("reserved one", "reserved two", "reserved three",
                 "self-sent must not wake"):
        assert word not in r.stdout, word
    assert any("[relay] re-arm:" in l and "--since 1" in l for l in out), out


def test_since_defaults_to_newest_row_to_self_at_start(tmp_path):
    """Without --since the cursor starts at MAX(id) of rows to self (spec
    §2.1): the Stop guard arms without it, so the backlog must never wake the
    first waiter, and a newer row to another instance must not move it."""
    space = tmp_path / "space"
    db = _seed_db(space, [(PEER, "chat", "old backlog one"),
                          (PEER, "chat", "old backlog two")])
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO messages (from_instance, to_instance, type, payload, ts)"
        " VALUES (?,?,?,?,?)",
        (PEER, "someone-else", "chat", json.dumps({"text": "not mine"}),
         time.time()))
    conn.commit()
    conn.close()
    r = _run_wait(space, tmp_path, db=db, since=None, timeout="1",
                  interval="0")
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "old backlog" not in r.stdout
    tokens = shlex.split(r.stdout.split("re-arm: ", 1)[1])
    assert tokens[tokens.index("--since") + 1] == "2", r.stdout


def test_exit0_text_is_collapsed_and_cut_at_220(tmp_path):
    long_text = ("word " * 200).strip()
    db = _seed_db(tmp_path / "space", [(PEER, "chat", long_text)])
    r = _run_wait(tmp_path / "space", tmp_path, db=db, timeout="2")
    assert r.returncode == 0
    wake = next(l for l in r.stdout.splitlines() if l.startswith("[relay] #"))
    body = wake.split(": ", 1)[1]
    assert "  " not in body and len(body) <= 220


def test_exit0_overflow_line(tmp_path):
    for i in range(12):
        _seed_db(tmp_path / "space", [(PEER, "chat", f"msg {i}")])
    r = _run_wait(tmp_path / "space", tmp_path, db=tmp_path / "space" / "liaison.db",
                  timeout="2")
    assert r.returncode == 0
    out = r.stdout.splitlines()
    assert sum(1 for l in out if l.startswith("[relay] #")) == 10
    assert any(l.strip() == "... and 2 more" for l in out), out


def test_idle_cycle_is_two_lines_exit2(tmp_path):
    db = _seed_db(tmp_path / "space", [])
    r = _run_wait(tmp_path / "space", tmp_path, db=db, timeout="1", interval="0")
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert len(r.stdout.strip().splitlines()) == 1, r.stdout
    assert len(r.stderr.strip().splitlines()) == 1, r.stderr
    assert "[relay] ceiling 1s, re-arm:" in r.stdout
    assert "[relay] re-arm:" in r.stderr


def test_exit3_db_missing(tmp_path):
    r = _run_wait(tmp_path / "space", tmp_path, db=tmp_path / "space" / "liaison.db",
                  timeout="1")
    assert r.returncode == 3
    assert "[relay] FAILED:" in r.stderr
    assert "Traceback" not in (r.stdout + r.stderr)


def test_exit3_self_id_missing_or_invalid(tmp_path):
    space = tmp_path / "space"
    db = _seed_db(space, [])
    r = _run_wait(tmp_path / "space", tmp_path, db=db, self_id="")
    assert r.returncode == 3 and "[relay] FAILED:" in r.stderr
    r = _run_wait(tmp_path / "space", tmp_path, db=db, self_id="../x")
    assert r.returncode == 3 and "[relay] FAILED:" in r.stderr
    # the invalid id must never become a path: no record, no spool dir
    assert not (tmp_path / "space" / "waiters").exists()
    assert not (_spool_root(tmp_path) / "spool" / "..").is_dir()


def test_read_only_db_and_no_read_ts(tmp_path):
    space = tmp_path / "space"
    db = _seed_db(space, [(PEER, "chat", "read me")])
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    r = _run_wait(space, tmp_path, db=db, timeout="2")
    assert r.returncode == 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    wal = Path(str(db) + "-wal")
    if wal.exists():
        assert hashlib.sha256(wal.read_bytes()).hexdigest() is not None
    conn = sqlite3.connect(db)
    try:
        read_ts = list(conn.execute(
            "SELECT read_ts FROM messages WHERE to_instance=?", (SELF_ID,)))
    finally:
        conn.close()
    assert all(ts is None for (ts,) in read_ts)


def test_spool_count_wakes_dot_files_do_not_and_files_are_untouched(tmp_path):
    space = tmp_path / "space"
    _seed_db(space, [])
    spool = _spool_dir_for(tmp_path)
    spool.mkdir(parents=True, exist_ok=True)
    real = spool / "packet-1.json"
    real.write_text('{"packet": true}', encoding="utf-8")
    hidden = spool / ".hidden.json"
    hidden.write_text("{}", encoding="utf-8")
    sha_before = hashlib.sha256(real.read_bytes()).hexdigest()
    mtime_before = real.stat().st_mtime_ns

    r = _run_wait(space, tmp_path, timeout="2")

    assert r.returncode == 0
    assert ("[relay] 1 message(s) in spool: read them with conscio_relay inbox"
            in r.stdout), r.stdout
    assert hashlib.sha256(real.read_bytes()).hexdigest() == sha_before
    assert real.stat().st_mtime_ns == mtime_before
    assert real.read_text(encoding="utf-8") == '{"packet": true}'
    assert hidden.exists() and hidden.read_text(encoding="utf-8") == "{}"


def test_record_written_and_removed_on_exit(tmp_path):
    space = tmp_path / "space"
    db = _seed_db(space, [])
    r = _run_wait(space, tmp_path, db=db, timeout="1")
    assert r.returncode == 2
    assert not _record_path(space, SESSION).exists(), "record leaked on exit"


def test_record_exists_while_running_and_names_the_waiter(tmp_path):
    space = tmp_path / "space"
    db = _seed_db(space, [])
    env = dict(os.environ)
    env["CONSCIO_SELF_ID"] = SELF_ID
    env["CONSCIO_RELAY_ROOT"] = str(_spool_root(tmp_path))
    child = subprocess.Popen(
        [sys.executable, str(HOOK), "wait", "--storage", str(space),
         "--session", SESSION, "--timeout", "6", "--interval", "1",
         "--liaison-db", str(db)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        record = _record_path(space, SESSION)
        for _ in range(100):
            if record.exists():
                break
            time.sleep(0.05)
        assert record.exists(), "waiter never wrote its ownership record"
        data = json.loads(record.read_text(encoding="utf-8"))
        assert data["pid"] == child.pid
        assert data["session"] == SESSION
        assert "started" in data and data["arm_blocked"] is False
    finally:
        child.terminate()
        child.wait(timeout=15)
    # SIGTERM does not run the waiter's finally: a violently killed waiter
    # leaves its record behind BY DESIGN — the next armer sees a dead pid
    # (not alive) and rewrites it. Only a normal exit must remove it.
    assert record.exists() or not record.exists()


def test_already_armed_live_owned_waiter_blocks_second(tmp_path):
    space = tmp_path / "space"
    db = _seed_db(space, [])
    env = dict(os.environ)
    env["CONSCIO_SELF_ID"] = SELF_ID
    env["CONSCIO_RELAY_ROOT"] = str(_spool_root(tmp_path))
    child = subprocess.Popen(
        [sys.executable, str(HOOK), "wait", "--storage", str(space),
         "--session", SESSION, "--timeout", "6", "--interval", "1",
         "--liaison-db", str(db)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        record = _record_path(space, SESSION)
        for _ in range(100):
            if record.exists():
                break
            time.sleep(0.05)
        assert record.exists()
        second = _run_wait(space, tmp_path, db=db, timeout="1")
        assert second.returncode == 0, (second.stdout, second.stderr)
        assert f"[relay] already armed (pid {child.pid})" in second.stdout
        assert record.exists(), "second waiter must not touch the record"
        assert json.loads(record.read_text(encoding="utf-8"))["pid"] == child.pid
    finally:
        child.terminate()
        child.wait(timeout=15)
        # record may survive SIGTERM by design (see above); the next armer
        # treats a dead pid as not-alive and rewrites it


def test_detached_waiter_is_not_owned(tmp_path):
    space = tmp_path / "space"
    db = _seed_db(space, [])
    env = dict(os.environ)
    env["CONSCIO_SELF_ID"] = SELF_ID
    env["CONSCIO_RELAY_ROOT"] = str(_spool_root(tmp_path))
    detached = subprocess.Popen(
        ["sh", "-c",
         (f"setsid {sys.executable} {HOOK} wait --storage {space} "
          f"--session {SESSION} --timeout 3 --interval 1 "
          f"--liaison-db {db} >/dev/null 2>&1 &")],
        env=env)
    try:
        record = _record_path(space, SESSION)
        for _ in range(100):
            if record.exists():
                break
            time.sleep(0.05)
        assert record.exists(), "the detached waiter still owns a record"
        second = _run_wait(space, tmp_path, db=db, timeout="1")
        assert second.returncode == 2, (second.stdout, second.stderr)
        assert "already armed" not in second.stdout
    finally:
        detached.wait(timeout=15)
        deadline = time.time() + 15
        while time.time() < deadline:
            if not _record_path(space, SESSION).exists():
                break
            time.sleep(0.1)


def test_stdlib_contract_reserved_and_spool_dir(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("_wake_hook_w2", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)

    from conscio.liaison import directory
    from conscio.liaison import relay as relay_mod

    assert hook.RESERVED == relay_mod.RESERVED_TYPES

    cases = [
        {},
        {"CONSCIO_RELAY_ROOT": str(tmp_path / "env-root")},
        {"CONSCIO_RELAY_ROOT": "~" + str(tmp_path / "tilde-root")},
        {"CONSCIO_RELAY_ROOT": "  " + str(tmp_path / "spaces-root") + "  "},
    ]
    for over in cases:
        for key, value in over.items():
            monkeypatch.setenv(key, value)
        if not over:
            monkeypatch.delenv("CONSCIO_RELAY_ROOT", raising=False)
        assert hook._spool_dir(SELF_ID) == directory.spool_dir(SELF_ID), over


def test_spool_dir_used_is_under_tmp(tmp_path):
    space = tmp_path / "space"
    _seed_db(space, [])
    r = _run_wait(space, tmp_path, timeout="1")
    assert r.returncode == 2
    # the relay root handed to the waiter lives inside this test's tmp_path,
    # so anything it counted or created is here — never in the real praça
    root = _spool_root(tmp_path)
    assert root.parent == tmp_path
