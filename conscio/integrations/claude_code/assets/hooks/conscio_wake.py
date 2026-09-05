#!/usr/bin/env python3
"""Wake a live Claude Code session when a relay message arrives.

The reactor delivers a message and marks it read.  A session that was already
running never learns about it: by the time the agent looks at its inbox the row
is no longer unread.  This hook closes that gap.

It runs on ``Stop`` -- the moment the agent would end its turn.  If messages
arrived for this instance since *this session* last looked, the hook prints them
on stderr and exits 2, which Claude Code treats as "do not stop".  The agent
reads the message and answers on its own, with no human in the loop.

Two properties make it safe to leave armed:

* The cursor is per session and advances before the hook blocks, so the same
  message never blocks twice -- a turn cannot loop on it.
* Every failure path exits 0.  A broken relay ends the turn normally instead of
  trapping the session.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

CURSOR_NAME = "wake-cursor.json"
BUSY_TIMEOUT_MS = 5000
MAX_MESSAGES = 10  # a bigger burst is trimmed, never dumped whole
MAX_TEXT = 400  # characters kept per message
COLD_START_WINDOW_S = 1800  # a session with no cursor only sees recent traffic
MAX_TRACKED_SESSIONS = 50  # keeps the cursor file bounded


def _argv_opt(argv: list[str], name: str) -> str | None:
    if name in argv:
        idx = argv.index(name)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def _self_id(storage: Path) -> str | None:
    """This agent's instance id, from the environment or the space identity."""
    env = (os.environ.get("CONSCIO_SELF_ID") or "").strip()
    if env:
        return env
    try:
        data = json.loads((storage / "instance.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    iid = data.get("instance_id")
    return str(iid) if iid else None


def _load_cursors(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_cursor(path: Path, session: str, last_id: int) -> None:
    """Record how far this session has been woken, atomically and bounded."""
    cursors = _load_cursors(path)
    cursors[session] = {"last_id": int(last_id), "ts": time.time()}
    if len(cursors) > MAX_TRACKED_SESSIONS:
        ranked = sorted(
            cursors.items(),
            key=lambda kv: (kv[1] or {}).get("ts", 0) if isinstance(kv[1], dict) else 0,
            reverse=True,
        )
        cursors = dict(ranked[:MAX_TRACKED_SESSIONS])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cursors), encoding="utf-8")
    os.replace(tmp, path)


def _pending(db: Path, self_id: str, after: int | None) -> list[dict]:
    """Messages addressed to me that this session has not been woken for."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        if after is None:  # cold start: recent traffic only, never history
            sql = (
                "SELECT id, from_instance, type, payload FROM messages "
                "WHERE to_instance=? AND ts >= ? ORDER BY id"
            )
            args: tuple = (self_id, time.time() - COLD_START_WINDOW_S)
        else:
            sql = (
                "SELECT id, from_instance, type, payload FROM messages "
                "WHERE to_instance=? AND id > ? ORDER BY id"
            )
            args = (self_id, int(after))
        rows = list(conn.execute(sql, args))
    finally:
        conn.close()
    return [
        {"id": r[0], "from": r[1] or "peer", "type": r[2] or "relay", "payload": r[3]}
        for r in rows
    ]


def _max_id(db: Path) -> int:
    """Highest message id on record -- the baseline for a fresh session."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT MAX(id) FROM messages").fetchone()
        finally:
            conn.close()
    except Exception:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


def _sender_label(db: Path, instance_id: str) -> str:
    """A human name for the sender, falling back to a short id."""
    short = str(instance_id)[:12]
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT nome, familia FROM agents WHERE instance_id=?", (instance_id,)
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return short
    if not row:
        return short
    return (row[0] or "").strip() or (row[1] or "").strip() or short


def _text_of(payload: str | None) -> str:
    try:
        data = json.loads(payload or "{}")
    except Exception:
        return (payload or "").strip()[:MAX_TEXT]
    if not isinstance(data, dict):
        return str(data)[:MAX_TEXT]
    for key in ("text", "mensagem", "message", "body"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_TEXT]
    return json.dumps(data)[:MAX_TEXT]


def _render(rows: list[dict], db: Path) -> str:
    shown, hidden = rows[:MAX_MESSAGES], max(0, len(rows) - MAX_MESSAGES)
    plural = "s" if len(rows) != 1 else ""
    lines = [
        f"[conscio relay] {len(rows)} new message{plural} arrived while you were working.",
        "Read them and decide whether to answer (conscio_relay_send) before stopping.",
        "",
    ]
    for row in shown:
        lines.append(
            f"  #{row['id']} from {_sender_label(db, row['from'])}"
            f" [{row['type']}]: {_text_of(row['payload'])}"
        )
    if hidden:
        lines.append(f"  ... and {hidden} more, see conscio_relay_inbox.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    storage = _argv_opt(argv, "--storage")
    if not storage:
        return 0
    storage_path = Path(storage)
    db = Path(_argv_opt(argv, "--liaison-db") or storage_path / "liaison.db")
    if not db.exists():
        return 0
    if (storage_path / "wake-off").exists():  # opt-out for this space
        return 0

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    session = str(payload.get("session_id") or "").strip() or "unknown-session"

    self_id = _self_id(storage_path)
    if not self_id:
        return 0

    cursor_path = storage_path / CURSOR_NAME
    entry = _load_cursors(cursor_path).get(session)
    after = entry.get("last_id") if isinstance(entry, dict) else None

    rows = _pending(db, self_id, after)
    if not rows:
        if after is None:  # cold start with an empty inbox still needs a baseline
            _save_cursor(cursor_path, session, _max_id(db))
        return 0

    # Advance first: even if the wake below is ignored, this message never
    # blocks the session a second time.
    _save_cursor(cursor_path, session, rows[-1]["id"])
    sys.stderr.write(_render(rows, db) + "\n")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:  # a broken relay must never trap the session
        sys.exit(0)
