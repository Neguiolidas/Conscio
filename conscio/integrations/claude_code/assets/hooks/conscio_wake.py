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

import hashlib
import json
import os
import re
import shlex
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

# ── waiter (`wait` subcommand) — spec §2.1/§2.3 ──────────────────────────────
# A stdlib literal copy of conscio.liaison.relay.RESERVED_TYPES: this hook
# cannot import the package (the plugin ships standalone).
RESERVED = frozenset({"review_request", "review_verdict", "board.propose"})
_WAIT_MAX_SHOWN = 10  # rows printed on traffic; the rest is counted
_WAIT_TEXT = 220  # characters kept per message line
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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


def _resolve_storage(storage: str | Path | None) -> Path | None:
    here = Path(__file__).resolve().parent
    obsstore_path = here / "conscio_obsstore.py"
    if obsstore_path.exists():
        import importlib.util
        try:
            spec = importlib.util.spec_from_file_location("_conscio_obsstore_wake", obsstore_path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod.resolve_hook_storage(storage)
        except Exception:
            pass
    return Path(storage).expanduser() if storage else None


def main(argv: list[str]) -> int:
    storage = _argv_opt(argv, "--storage")
    if not storage:
        return 0
    resolved = _resolve_storage(storage)
    if resolved is None:
        return 0
    storage_path = resolved
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


# ── waiter (`wait` subcommand) — spec §2.1/§2.3 ──────────────────────────────

def _spool_dir(self_id: str) -> Path:
    """Stdlib copy of conscio.liaison.directory.spool_dir (same env + default)."""
    raw = (os.environ.get("CONSCIO_RELAY_ROOT") or "").strip()
    root = Path(raw).expanduser() if raw else Path.home() / ".conscio" / "relay"
    return root / "spool" / self_id


def _record_path(storage: Path, session: str) -> Path:
    key = hashlib.sha256(session.encode("utf-8")).hexdigest()[:16]
    return Path(storage) / "waiters" / f"{key}.json"


def _read_record_pid(storage: Path, session: str) -> int | None:
    try:
        data = json.loads(
            _record_path(storage, session).read_text(encoding="utf-8"))
        pid = int(data.get("pid"))
        return pid if pid > 0 else None
    except Exception:
        return None


def _write_record(storage: Path, session: str) -> None:
    """Atomic ownership record (tmp + os.replace), keyed by session."""
    path = _record_path(storage, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"pid": os.getpid(), "session": session,
           "started": time.time(), "arm_blocked": False}
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    os.replace(tmp, path)


def _drop_record(storage: Path, session: str) -> None:
    """Remove the record only if it still names this pid (Stop may have
    rewritten it with its own arm state)."""
    try:
        if _read_record_pid(storage, session) == os.getpid():
            _record_path(storage, session).unlink(missing_ok=True)
    except Exception:
        pass


def _chain(pid: int) -> list[int]:
    """`pid` and its ancestors, youngest first, from /proc ([] if no /proc)."""
    chain: list[int] = []
    seen: set[int] = set()
    cur = pid
    while cur > 0 and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        try:
            status = (Path(f"/proc/{cur}") / "status").read_text(encoding="utf-8")
            match = re.search(r"^PPid:\s+(\d+)", status, re.MULTILINE)
            cur = int(match.group(1)) if match else 0
        except Exception:
            break
    return chain


def _comm(pid: int) -> str:
    try:
        return (Path(f"/proc/{pid}") / "comm").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _lca_ok(pid: int, me: int) -> bool:
    """The lowest common ancestor of `pid` and `me` is neither pid 1 nor a
    process whose comm is `systemd` (C4: a setsid/nohup waiter fails this)."""
    mine = set(_chain(me))
    for node in _chain(pid):
        if node in mine:
            return not (node == 1 or _comm(node) == "systemd")
    return False


def _alive_owned(pid: int, session: str, me: int) -> bool:
    """A live waiter on MY chain: /proc exists, its cmdline names hook+wait+
    session, and the LCA rule holds."""
    if pid <= 0 or not Path(f"/proc/{pid}").exists():
        return False
    try:
        cmdline = (Path(f"/proc/{pid}")
                   / "cmdline").read_bytes().decode("utf-8", "replace")
    except Exception:
        return False
    if not ("conscio_wake.py" in cmdline and "wait" in cmdline
            and session in cmdline):
        return False
    return _lca_ok(pid, me)


def _wait_pending(db: Path, self_id: str, since: int) -> list[dict]:
    """Read-only query: rows for me newer than `since`, self-sent excluded,
    reserved types filtered with the stdlib copy."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        rows = list(conn.execute(
            "SELECT id, from_instance, type, payload FROM messages "
            "WHERE to_instance=? AND id > ? AND from_instance != ? ORDER BY id",
            (self_id, int(since), self_id)))
    finally:
        conn.close()
    return [{"id": r[0], "from": r[1] or "peer", "type": r[2] or "relay",
             "payload": r[3]}
            for r in rows if (r[2] or "relay") not in RESERVED]


def _max_self_id(db: Path, self_id: str) -> int:
    """Read-only: the newest row id addressed to me, 0 on an empty mailbox.
    The default cursor, so the backlog never wakes a freshly armed waiter."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        row = conn.execute("SELECT MAX(id) FROM messages WHERE to_instance=?",
                           (self_id,)).fetchone()
    finally:
        conn.close()
    return int(row[0] or 0)


def _spool_count(spool: Path) -> int:
    """*.json packets in the own spool dir; dot-files skipped, never opened."""
    try:
        return sum(1 for p in spool.iterdir()
                   if p.is_file() and not p.name.startswith(".")
                   and p.name.endswith(".json"))
    except Exception:
        return 0


def _wait_text(payload: str | None) -> str:
    return " ".join(_text_of(payload).split())[:_WAIT_TEXT]


def _arm_command(hook: Path, storage: Path, session: str, since: int,
                 timeout: int, interval: int, db: Path | None) -> str:
    """The exact re-arm command: hook's own resolved path, quoted values."""
    parts = [
        "python3", shlex.quote(str(hook)), "wait",
        "--storage", shlex.quote(str(storage)),
        "--session", shlex.quote(session),
        "--since", shlex.quote(str(since)),
        "--timeout", shlex.quote(str(timeout)),
        "--interval", shlex.quote(str(interval)),
    ]
    if db is not None:
        parts += ["--liaison-db", shlex.quote(str(db))]
    return " ".join(parts)


def _cmd_wait(argv: list[str]) -> int:
    """Long-lived waiter: announce traffic on stdout, ceiling at the timeout.
    Read-only on the db; never ingests, never marks read."""
    hook = Path(__file__).resolve()
    try:
        storage_raw = _argv_opt(argv, "--storage")
        session = (_argv_opt(argv, "--session") or "").strip()
        if not storage_raw:
            sys.stderr.write("[relay] FAILED: no --storage\n")
            return 3
        if not session:
            sys.stderr.write("[relay] FAILED: no --session\n")
            return 3
        storage = _resolve_storage(storage_raw)
        if storage is None:
            sys.stderr.write("[relay] FAILED: storage could not be resolved\n")
            return 3
        db = Path(_argv_opt(argv, "--liaison-db") or storage / "liaison.db")
        if not db.exists():
            sys.stderr.write(f"[relay] FAILED: liaison db missing at {db}\n")
            return 3
        self_id = _self_id(storage)
        if not self_id:
            sys.stderr.write("[relay] FAILED: self id missing\n")
            return 3
        if not _ID_RE.fullmatch(self_id):
            sys.stderr.write("[relay] FAILED: self id invalid\n")
            return 3
        try:
            timeout = int(_argv_opt(argv, "--timeout") or 1800)
            interval = max(0, int(_argv_opt(argv, "--interval") or 2))
            since_arg = _argv_opt(argv, "--since")
            since = int(since_arg) if since_arg else _max_self_id(db, self_id)
        except ValueError:
            sys.stderr.write("[relay] FAILED: --timeout/--interval/--since "
                             "must be integers\n")
            return 3

        me = os.getpid()
        other = _read_record_pid(storage, session)
        if other is not None and other != me \
                and _alive_owned(other, session, me):
            sys.stdout.write(f"[relay] already armed (pid {other})\n")
            return 0

        _write_record(storage, session)
        try:
            deadline = time.monotonic() + timeout
            while True:
                rows = _wait_pending(db, self_id, since)
                spool = _spool_count(_spool_dir(self_id))
                if rows or spool:
                    shown = rows[:_WAIT_MAX_SHOWN]
                    lines = [
                        f"[relay] #{r['id']} from {_sender_label(db, r['from'])}"
                        f" [{r['type']}]: {_wait_text(r['payload'])}"
                        for r in shown
                    ]
                    hidden = len(rows) - len(shown)
                    if hidden:
                        lines.append(f"... and {hidden} more")
                    if spool:
                        lines.append(
                            f"[relay] {spool} message(s) in spool: "
                            "read them with conscio_relay inbox")
                    max_seen = max([since] + [r["id"] for r in rows])
                    lines.append("[relay] re-arm: "
                                 + _arm_command(hook, storage, session,
                                                max_seen, timeout, interval,
                                                db))
                    sys.stdout.write("\n".join(lines) + "\n")
                    return 0
                if time.monotonic() >= deadline:
                    cmd = _arm_command(hook, storage, session, since, timeout,
                                       interval, db)
                    sys.stdout.write(f"[relay] ceiling {timeout}s, "
                                     f"re-arm: {cmd}\n")
                    sys.stderr.write(f"[relay] re-arm: {cmd}\n")
                    return 2
                time.sleep(interval)
        finally:
            _drop_record(storage, session)
    except Exception as exc:  # one line, no traceback: the armer decides
        sys.stderr.write(f"[relay] FAILED: {exc.__class__.__name__}\n")
        return 3


if __name__ == "__main__":
    try:
        argv = sys.argv[1:]
        if argv and argv[0] == "wait":
            sys.exit(_cmd_wait(argv[1:]))
        sys.exit(main(argv))
    except Exception:  # a broken relay must never trap the session
        sys.exit(0)
