"""The Stop hook that wakes a live session when a relay message arrives."""

from __future__ import annotations

import json
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


def _send(storage: Path, text: str, *, read: bool = True, age_s: float = 0.0) -> int:
    """Deliver a message the way the reactor does -- already marked read."""
    conn = sqlite3.connect(storage / "liaison.db")
    now = time.time()
    cur = conn.execute(
        "INSERT INTO messages (from_instance, to_instance, type, payload, ts, read_ts)"
        " VALUES (?,?,?,?,?,?)",
        (PEER, ME, "relay", json.dumps({"text": text}), now - age_s, now if read else None),
    )
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return mid


def _run(storage: Path, session: str = "s1") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK), "stop", "--storage", str(storage)],
        input=json.dumps({"session_id": session, "hook_event_name": "Stop"}),
        capture_output=True,
        text=True,
    )


def test_quiet_inbox_lets_the_turn_end(tmp_path):
    assert _run(_space(tmp_path)).returncode == 0


def test_message_already_marked_read_still_wakes_the_session(tmp_path):
    """The whole point: the reactor consumed it, the session must still see it."""
    storage = _space(tmp_path)
    _run(storage)  # session takes its baseline
    _send(storage, "voce esta ai?")

    res = _run(storage)

    assert res.returncode == 2, "exit 2 is what stops Claude from stopping"
    assert "voce esta ai?" in res.stderr
    assert "Peer-One" in res.stderr, "the sender is named, not just an id"


def test_same_message_never_wakes_twice(tmp_path):
    """A Stop hook that keeps blocking would trap the turn in a loop."""
    storage = _space(tmp_path)
    _run(storage)
    _send(storage, "ping")

    assert _run(storage).returncode == 2
    assert _run(storage).returncode == 0


def test_fresh_session_does_not_replay_history(tmp_path):
    storage = _space(tmp_path)
    _send(storage, "conversa de ontem", age_s=86_400)

    assert _run(storage, session="brand-new").returncode == 0


def test_baseline_survives_an_empty_cold_start(tmp_path):
    """Cold start with old mail must not leave a cursor that replays it later."""
    storage = _space(tmp_path)
    _send(storage, "conversa de ontem", age_s=86_400)

    assert _run(storage, session="s2").returncode == 0
    _send(storage, "mensagem de agora")
    res = _run(storage, session="s2")

    assert res.returncode == 2
    assert "mensagem de agora" in res.stderr
    assert "conversa de ontem" not in res.stderr


def test_each_session_is_woken_independently(tmp_path):
    storage = _space(tmp_path)
    _run(storage, session="a")
    _run(storage, session="b")
    _send(storage, "para todos")

    assert _run(storage, session="a").returncode == 2
    assert _run(storage, session="b").returncode == 2


def test_message_for_someone_else_is_ignored(tmp_path):
    storage = _space(tmp_path)
    _run(storage)
    conn = sqlite3.connect(storage / "liaison.db")
    conn.execute(
        "INSERT INTO messages (from_instance, to_instance, type, payload, ts, read_ts)"
        " VALUES (?,?,?,?,?,?)",
        (PEER, "outro-agente", "relay", json.dumps({"text": "nao e para mim"}), time.time(), None),
    )
    conn.commit()
    conn.close()

    assert _run(storage).returncode == 0


def test_a_burst_is_trimmed_not_dumped(tmp_path):
    storage = _space(tmp_path)
    _run(storage)
    for i in range(25):
        _send(storage, f"msg {i}")

    res = _run(storage)

    assert res.returncode == 2
    assert "25 new messages" in res.stderr
    assert "and 15 more" in res.stderr
    assert "msg 24" not in res.stderr, "only the first page is inlined"


def test_opt_out_file_disarms_the_hook(tmp_path):
    storage = _space(tmp_path)
    _run(storage)
    _send(storage, "silencio")
    (storage / "wake-off").touch()

    assert _run(storage).returncode == 0


@pytest.mark.parametrize(
    "breakage",
    [
        pytest.param(lambda s: (s / "liaison.db").unlink(), id="no-database"),
        pytest.param(lambda s: (s / "instance.json").unlink(), id="no-identity"),
        pytest.param(
            lambda s: (s / "liaison.db").write_bytes(b"not a database"), id="corrupt-database"
        ),
    ],
)
def test_a_broken_relay_never_traps_the_session(tmp_path, breakage):
    storage = _space(tmp_path)
    _send(storage, "oi")
    breakage(storage)

    assert _run(storage).returncode == 0, "failure must end the turn, not block it"


def test_unreadable_cursor_degrades_to_a_cold_start(tmp_path):
    """Losing the cursor may cost one extra wake -- it must not cost a loop."""
    storage = _space(tmp_path)
    _run(storage)
    _send(storage, "oi")
    (storage / "wake-cursor.json").write_text("{{{")

    assert _run(storage).returncode == 2
    assert _run(storage).returncode == 0, "the rewritten cursor stops the repeat"


def test_missing_storage_argument_is_harmless(tmp_path):
    res = subprocess.run(
        [sys.executable, str(HOOK), "stop"], input="{}", capture_output=True, text=True
    )
    assert res.returncode == 0
