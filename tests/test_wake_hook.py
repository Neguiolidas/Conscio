"""The Stop guard: never announces (C9); arms the waiter once, then stands down.

W3 rewrite. The old behaviour (query the mailbox on Stop, print the messages,
block with exit 2) is deleted together with its announcement tests: the WAITER
now does the announcing, and Stop only guarantees a live, owned waiter exists.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOK = (
    Path(__file__).resolve().parents[1]
    / "conscio/integrations/claude_code/assets/hooks/conscio_wake.py"
)
ME = "11111111-2222-3333-4444-555555555555"
PEER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SESSION = "s1"
NO_WAITER = "[conscio relay] no waiter for this session. Arm it now with your background tool (run_in_background):"


def _space(tmp_path: Path) -> Path:
    storage = tmp_path / "space"
    storage.mkdir()
    (storage / "instance.json").write_text(json.dumps({"instance_id": ME}))
    conn = sqlite3.connect(storage / "liaison.db")
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " from_instance TEXT, to_instance TEXT, type TEXT, payload TEXT,"
        " ts REAL, read_ts REAL)"
    )
    conn.execute("CREATE TABLE agents (instance_id TEXT PRIMARY KEY, nome TEXT, familia TEXT)")
    conn.execute("INSERT INTO agents VALUES (?,?,?)", (PEER, "Peer-One", "hermes"))
    conn.commit()
    conn.close()
    return storage


def _send(storage: Path, text: str, *, read: bool = True) -> int:
    """Deliver a message the way the reactor does -- already marked read."""
    conn = sqlite3.connect(storage / "liaison.db")
    cur = conn.execute(
        "INSERT INTO messages (from_instance, to_instance, type, payload, ts, read_ts)"
        " VALUES (?,?,?,?,?,?)",
        (PEER, ME, "relay", json.dumps({"text": text}), time.time(),
         time.time() if read else None),
    )
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return mid


def _make_spool(tmp_path: Path, self_id: str = ME) -> Path:
    spool = tmp_path / "relay-praca" / "spool" / self_id
    spool.mkdir(parents=True, exist_ok=True)
    return spool


def _run_stop(storage: Path, tmp_path: Path, *, session: str = SESSION,
              with_spool: bool = True, stdin: str | None = None,
              env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CONSCIO_RELAY_ROOT"] = str(tmp_path / "relay-praca")
    if with_spool:
        _make_spool(tmp_path)
    env.update(env_extra or {})
    payload = json.dumps({"session_id": session, "hook_event_name": "Stop"})
    return subprocess.run(
        [sys.executable, str(HOOK), "stop", "--storage", str(storage)],
        input=stdin if stdin is not None else payload,
        capture_output=True, text=True, env=env, timeout=60,
    )


def _record(space: Path, session: str = SESSION) -> Path:
    key = hashlib.sha256(session.encode("utf-8")).hexdigest()[:16]
    return space / "waiters" / f"{key}.json"


def _read_record(space: Path, session: str = SESSION) -> dict | None:
    try:
        return json.loads(_record(space, session).read_text(encoding="utf-8"))
    except Exception:
        return None


def _arming_command(res: subprocess.CompletedProcess) -> str:
    match = re.search(r"run_in_background\): (.+)", res.stderr)
    assert match, res.stderr
    return match.group(1).strip()


def _spawn_waiter(space: Path, tmp_path: Path, *, session: str = SESSION,
                  timeout: str = "6") -> subprocess.Popen:
    env = dict(os.environ)
    env["CONSCIO_RELAY_ROOT"] = str(tmp_path / "relay-praca")
    _make_spool(tmp_path)
    return subprocess.Popen(
        [sys.executable, str(HOOK), "wait", "--storage", str(space),
         "--session", session, "--timeout", timeout, "--interval", "1",
         "--liaison-db", str(space / "liaison.db")],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)


def _wait_for_record(space: Path, session: str = SESSION) -> dict:
    for _ in range(100):
        data = _read_record(space, session)
        if data:
            return data
        time.sleep(0.05)
    raise AssertionError("waiter never wrote its ownership record")


# ── the guard's fail-open edges ──────────────────────────────────────────────

def test_no_proc_exits_zero(tmp_path, monkeypatch):
    """C8: without /proc the guard cannot tell live from dead -- it exits 0."""
    spec = importlib.util.spec_from_file_location("_wake_hook_w3", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    monkeypatch.setattr(hook, "PROC_ROOT", Path("/proc-definitely-missing"))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(
        json.dumps({"session_id": SESSION})))
    monkeypatch.setenv("CONSCIO_RELAY_ROOT", str(tmp_path / "relay-praca"))
    storage = _space(tmp_path)
    _send(storage, "oi")
    _make_spool(tmp_path)
    assert hook.main(["stop", "--storage", str(storage)]) == 0


def test_garbage_stdin_or_missing_session_exits_zero(tmp_path):
    storage = _space(tmp_path)
    _send(storage, "oi")
    res = _run_stop(storage, tmp_path, stdin="not json at all")
    assert res.returncode == 0
    res = _run_stop(storage, tmp_path, stdin=json.dumps({"hook_event_name": "Stop"}))
    assert res.returncode == 0
    assert not _record(space=storage).exists()


def test_missing_storage_argument_is_harmless(tmp_path):
    res = subprocess.run(
        [sys.executable, str(HOOK), "stop"], input="{}",
        capture_output=True, text=True)
    assert res.returncode == 0


def test_self_id_unresolved_exits_zero(tmp_path):
    storage = _space(tmp_path)
    (storage / "instance.json").unlink()
    res = _run_stop(storage, tmp_path)
    assert res.returncode == 0
    assert not _record(space=storage).exists()


@pytest.mark.parametrize("bad_id", [".", ".."])
def test_self_id_the_waiter_refuses_exits_zero(tmp_path, bad_id):
    """``spool/.`` and ``spool/..`` are real directories, so the spool check
    alone would pass them, and the guard would ask for a waiter that exits 3
    on the same id -- a blocked turn every other Stop, forever. An id the
    waiter refuses counts as unresolved."""
    storage = _space(tmp_path)
    res = _run_stop(storage, tmp_path, env_extra={"CONSCIO_SELF_ID": bad_id})
    assert res.returncode == 0, res.stderr
    assert res.stderr == ""
    assert not _record(space=storage).exists()


def test_no_spool_dir_exits_zero_and_writes_nothing(tmp_path):
    storage = _space(tmp_path)
    _send(storage, "oi")
    res = _run_stop(storage, tmp_path, with_spool=False)
    assert res.returncode == 0
    assert not _record(space=storage).exists(), \
        "no spool dir: the guard must not write an ownership record"


# ── the arming alternation (C5) ──────────────────────────────────────────────

def test_no_waiter_blocks_once_then_stands_down(tmp_path):
    """First Stop with no waiter: block once with the arming command. Next
    Stop: stand down (C5, at most one forced turn end in a row). The one
    after: block again (the waiter was still not armed)."""
    storage = _space(tmp_path)
    _send(storage, "voce esta ai?")

    first = _run_stop(storage, tmp_path)
    assert first.returncode == 2
    assert NO_WAITER in first.stderr
    assert first.stdout == "", "C9: Stop never writes stdout"
    record = _read_record(storage)
    assert record is not None and record["arm_blocked"] is True
    assert record["pid"] is None, "Stop must never record its own pid"
    cmd = _arming_command(first)
    assert "conscio_wake.py" in cmd and "wait" in cmd
    assert f"--session {SESSION}" in cmd
    assert "--since" not in cmd, "addendum 2: the printed command has no --since"
    assert "--liaison-db" not in cmd, "addendum 2: Stop never opens the db"

    second = _run_stop(storage, tmp_path)
    assert second.returncode == 0, "C5: never block twice in a row"
    assert _read_record(storage)["arm_blocked"] is False

    third = _run_stop(storage, tmp_path)
    assert third.returncode == 2, "still no waiter: block again"


def test_stop_never_prints_pending_messages(tmp_path):
    """C9: the announcement is the waiter's job. Even with unread mail waiting,
    the guard prints only the arming command."""
    storage = _space(tmp_path)
    _send(storage, "voce esta ai?", read=False)

    res = _run_stop(storage, tmp_path)

    assert res.returncode == 2
    assert res.stdout == ""
    assert "voce esta ai?" not in res.stderr
    assert "Peer-One" not in res.stderr


def test_corrupt_database_changes_nothing_for_the_guard(tmp_path):
    """C9 by construction: Stop never opens the db, so a corrupt one cannot
    trap the session nor change the guard's decision."""
    storage = _space(tmp_path)
    _send(storage, "oi")
    (storage / "liaison.db").write_bytes(b"not a database")

    first = _run_stop(storage, tmp_path)
    assert first.returncode == 2
    assert _run_stop(storage, tmp_path).returncode == 0


# ── live owned waiter ────────────────────────────────────────────────────────

def test_live_owned_waiter_clears_arm_blocked(tmp_path):
    """A live waiter on the session chain: exit 0, arm_blocked cleared, and
    the waiter's pid preserved in the record it owns."""
    storage = _space(tmp_path)
    child = _spawn_waiter(storage, tmp_path)
    try:
        data = _wait_for_record(storage)
        assert data["pid"] == child.pid and data["arm_blocked"] is False
        res = _run_stop(storage, tmp_path)
        assert res.returncode == 0
        after = _read_record(storage)
        assert after is not None
        assert after["pid"] == child.pid, "Stop must preserve the waiter's pid"
        assert after["arm_blocked"] is False
    finally:
        # the waiter exits NORMALLY at its own ceiling: its finally removes
        # the record (addendum: live waiter + Stop clears → record is gone)
        child.wait(timeout=15)
    assert not _record(space=storage).exists()


def test_detached_waiter_is_not_owned_and_still_blocks(tmp_path):
    """C4: a setsid waiter is reparented and fails the LCA rule — the guard
    must still block (the session has no waiter it can trust)."""
    storage = _space(tmp_path)
    env = dict(os.environ)
    env["CONSCIO_RELAY_ROOT"] = str(tmp_path / "relay-praca")
    _make_spool(tmp_path)
    detached = subprocess.Popen(
        ["sh", "-c",
         (f"setsid {sys.executable} {HOOK} wait --storage {storage} "
          f"--session {SESSION} --timeout 4 --interval 1 "
          f"--liaison-db {storage / 'liaison.db'} >/dev/null 2>&1 &")],
        env=env)
    try:
        for _ in range(100):
            if _record(space=storage).exists():
                break
            time.sleep(0.05)
        res = _run_stop(storage, tmp_path)
        assert res.returncode == 2, "detached waiter: not owned, still blocks"
        assert "already armed" not in res.stderr
    finally:
        detached.wait(timeout=15)
        deadline = time.time() + 15
        while time.time() < deadline and _record(space=storage).exists():
            time.sleep(0.1)


def test_sessions_are_independent(tmp_path):
    """C7: records are keyed by session — arming session A must never stand
    down session B, and A's record bytes stay untouched by B's cycle."""
    storage = _space(tmp_path)
    _run_stop(storage, tmp_path, session="a")          # a: arm_blocked true
    before = _record(space=storage, session="a").read_bytes()

    res = _run_stop(storage, tmp_path, session="b")
    assert res.returncode == 2, "session b has no waiter of its own"

    assert _record(space=storage, session="a").read_bytes() == before


# ── deleted behaviour, pinned as deleted ─────────────────────────────────────

def test_wake_off_file_has_no_effect_and_no_cursor_is_ever_created(tmp_path):
    """The opt-out file and the cursor are deleted with the old behaviour:
    the guard behaves identically with a wake-off present, and no
    wake-cursor.json is ever written again."""
    storage = _space(tmp_path)
    _send(storage, "silencio")
    (storage / "wake-off").touch()

    res = _run_stop(storage, tmp_path)

    assert res.returncode == 2, "wake-off is gone: the guard behaves the same"
    assert not (storage / "wake-cursor.json").exists()


def test_broken_identity_still_fails_open(tmp_path):
    storage = _space(tmp_path)
    (storage / "instance.json").write_text("{ corrupted")
    res = _run_stop(storage, tmp_path)
    assert res.returncode == 0


# ── end to end: the printed command arms an owned waiter ────────────────────

def test_printed_command_arms_an_owned_waiter_ignoring_backlog(tmp_path):
    """The command printed on stderr, run as-is, starts a waiter the next
    Stop sees as owned. Addendum 2: the waiter starts at MAX(id) to self, so
    a pre-seeded backlog never wakes it (and mutation M7 --since 0 fails this)."""
    storage = _space(tmp_path)
    _send(storage, "backlog de ontem")
    _send(storage, "backlog de hoje")

    first = _run_stop(storage, tmp_path)
    assert first.returncode == 2
    cmd = _arming_command(first)
    assert "--since" not in cmd and "--liaison-db" not in cmd

    env = dict(os.environ)
    env["CONSCIO_RELAY_ROOT"] = str(tmp_path / "relay-praca")
    waiter = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, env=env, text=True)
    try:
        _wait_for_record(storage)
        second = _run_stop(storage, tmp_path)
        assert second.returncode == 0, "the armed waiter is live and owned"
        try:
            waiter.wait(timeout=2)
            raise AssertionError("backlog woke the waiter (M7 shape)")
        except subprocess.TimeoutExpired:
            pass  # still running: the MAX(id) start ignored the backlog
    finally:
        waiter.terminate()
        try:
            waiter.wait(timeout=15)
        except subprocess.TimeoutExpired:
            waiter.kill()
    # the record may survive this SIGTERM by design; the next armer sees a
    # dead pid (not alive) and rewrites it
