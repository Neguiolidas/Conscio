#!/usr/bin/env python3
"""Wake plumbing for a live Claude Code session.

Two subcommands, one file:

* ``stop`` -- the guard (spec §2.2). Runs on ``Stop``, the moment the agent
  would end its turn. It NEVER announces: no mailbox query, no message text
  (C9 by construction). It only guarantees a live, owned waiter exists: with
  none, it blocks once (exit 2, stderr carries the exact arming command the
  agent runs with its background tool); with the waiter armed it stands down
  (exit 0) and the waiter -- never the hook -- announces traffic.
* ``wait`` -- the waiter (spec §2.1). A long-lived, read-only process: it
  polls the agent's own mailbox and spool, exits 0 with one line per message
  when something arrives for this instance, 2 on the ceiling with the exact
  re-arm command, and 3 on failure. Reserved and self-sent rows never wake
  it. Spool files are counted, never opened or ingested.
* ``react`` -- the Codex reactor (Codex plan §3.2 as amended by §6). A
  long-lived, read-only process: when unread relay messages addressed to this
  agent arrive, it queues ONE notice per outstanding batch to the Codex
  session via ``codex queue --thread <session> --message "relay: <Name>, …
  replied"``. The notice is a label, never message content or ids. The
  Codex core idle-gates and keeps the queue durable, so the reactor does
  not detect idle itself and does not re-queue an outstanding batch. It
  exits when the owning Codex process (``--owner-pid``) dies.

Two properties make the pair safe to leave armed:

* ``arm_blocked`` alternates: the guard blocks at most one turn end in a row,
  so a session that never arms still ends its turn.
* Every unexpected failure exits 0 from ``stop`` (a broken relay must never
  trap a session) and 3 from ``wait`` (the armer decides what to do).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROC_ROOT = Path("/proc")  # one literal; tests point it at a missing dir (C8)
BUSY_TIMEOUT_MS = 5000
MAX_TEXT = 400  # characters kept per message
_WAIT_DEFAULT_TIMEOUT_S = 1800
_WAIT_DEFAULT_INTERVAL_S = 2

# ── waiter (`wait` subcommand) — spec §2.1/§2.3 ──────────────────────────────
# A stdlib literal copy of conscio.liaison.relay.RESERVED_TYPES: this hook
# cannot import the package (the plugin ships standalone).
RESERVED = frozenset({"review_request", "review_verdict", "board.propose"})
_WAIT_MAX_SHOWN = 10  # rows printed on traffic; the rest is counted
_WAIT_TEXT = 220  # characters kept per message line
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_REACT_DEBOUNCE_S = 3.0  # wait after a fresh batch before queuing, to fold a
                        # burst into one notice (override: CONSCIO_REACT_DEBOUNCE_S)
_REACT_RETRY_BASE_S = 60.0   # queue-failure backoff: 60 s, doubling, capped
_REACT_RETRY_CAP_S = 900.0   # at 15 min (overrides: CONSCIO_REACT_RETRY_{BASE,CAP}_S)
_REACT_RENOTIFY_S = 1800.0   # safety: an outstanding notice still unread after
                             # 30 min re-queues ONE notice (override: CONSCIO_REACT_RENOTIFY_S)


def _react_renotify_s() -> float:
    raw = (os.environ.get("CONSCIO_REACT_RENOTIFY_S") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            return _REACT_RENOTIFY_S
    return _REACT_RENOTIFY_S


def notice_text(names: list[str]) -> str:
    """The text queued to wake the agent: ``relay: <Name> replied`` for one
    sender, ``relay: <Name1>, <Name2> replied`` for several. Names are the
    sender LABELS (unique, in arrival order) -- never message ids, never tool
    names, never message content. When no sender label is known (the message is
    still only a spool file, no db row yet) the notice degrades to
    ``relay: new message`` -- never empty."""
    uniq: list[str] = []
    for name in names:
        name = (name or "").strip()
        if name and name not in uniq:
            uniq.append(name)
    if not uniq:
        return "relay: new message"
    return "relay: " + ", ".join(uniq) + " replied"


def react_notice_decision(pending: bool, outstanding: bool, can_attempt: bool,
                          now: float, outstanding_since: float | None,
                          renotify_s: float) -> bool:
    """Whether to queue a notice now -- the single-latch decision.

    ``pending`` is True while ANYTHING is unread: spool>0 OR an unread db row
    (a peer's message reaches the Codex space FIRST as a spool file; the db row
    only appears after the agent calls ``inbox``). A notice is ``outstanding``
    from the moment one goes out until there is nothing unread left; while it
    is outstanding NOTHING else goes out, whether the new content is spool or
    db (the queued notice is durable and its turn reads the whole inbox).

    Safety (plan §3.2): an outstanding notice still unread after ``renotify_s``
    (30 min) re-queues ONE notice and restarts the clock -- this covers a turn
    the human interrupted, or a Codex that ignored the notice, so the latch
    never blocks the agent forever. ``can_attempt`` is False inside a queue
    backoff, in which case a send is held, not dropped.

    The caller clears the latch (+``outstanding_since``) when pending drops to
    False, and sets both on a successful send (which (re)starts the clock)."""
    if outstanding and not pending:
        return False                       # all read: caller clears the latch
    if outstanding:
        # still pending: only the timed safety re-notify fires, one at a time
        return (outstanding_since is not None
                and (now - outstanding_since) >= renotify_s and can_attempt)
    return pending and can_attempt          # a fresh batch: one notice


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


def _set_arm_blocked(storage: Path, session: str, record: dict | None,
                     blocked: bool) -> None:
    """Flip arm_blocked in the SAME record the waiter writes, atomically.

    The waiter's pid is PRESERVED (so its `_drop_record` still removes the
    record on exit); `pid: null` when there was no record -- never this
    Stop hook's own pid, which must never look like a waiter."""
    rec = dict(record) if isinstance(record, dict) else {
        "session": session, "started": time.time()}
    rec["pid"] = rec["pid"] if isinstance(rec.get("pid"), int) else None
    rec["arm_blocked"] = blocked
    path = _record_path(storage, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    """Stop guard (spec §2.2): never announces, never opens the db (C9).

    Order: no /proc -> 0; storage, self id or session unresolved or stdin
    not JSON -> 0; the own spool dir missing -> 0 (a plugin user who never
    joined a relay is never asked to arm anything). A live owned waiter ->
    clear arm_blocked, exit 0. Else arm_blocked set -> clear it, exit 0
    (C5: at most one forced turn end in a row). Else set arm_blocked, exit 2
    with the exact arming command on stderr."""
    if not PROC_ROOT.exists():
        return 0                                  # C8: cannot tell live from dead
    storage_raw = _argv_opt(argv, "--storage")
    resolved = _resolve_storage(storage_raw) if storage_raw else None
    if resolved is None:
        return 0
    storage = resolved

    self_id = _self_id(storage)
    if not self_id or not _ID_RE.fullmatch(self_id):
        return 0                                  # an id the waiter refuses
    if not _spool_dir(self_id).is_dir():
        return 0                                  # not on the relay (C6)

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    session = str(payload.get("session_id") or "").strip()
    if not session:
        return 0

    record = None
    try:
        record = json.loads(
            _record_path(storage, session).read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            record = None
    except Exception:
        record = None
    waiter_pid = record.get("pid") if record else None
    arm_blocked = bool(record.get("arm_blocked")) if record else False

    if isinstance(waiter_pid, int) and waiter_pid > 0 \
            and _alive_owned(waiter_pid, session, os.getpid()):
        if arm_blocked:                           # waiter owns it: stand down
            _set_arm_blocked(storage, session, record, False)
        return 0
    if arm_blocked:                               # C5: one forced turn per block
        _set_arm_blocked(storage, session, record, False)
        return 0

    _set_arm_blocked(storage, session, record, True)
    cmd = _arm_command(hook=Path(__file__).resolve(), storage=storage,
                       session=session, since=None,
                       timeout=_WAIT_DEFAULT_TIMEOUT_S,
                       interval=_WAIT_DEFAULT_INTERVAL_S, db=None)
    sys.stderr.write(
        "[conscio relay] no waiter for this session. Arm it now with your "
        f"background tool (run_in_background): {cmd}\n")
    return 2


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
        pid = data.get("pid")
        return int(pid) if isinstance(pid, int) and pid > 0 else None
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
            status = (PROC_ROOT / f"{cur}" / "status").read_text(encoding="utf-8")
            match = re.search(r"^PPid:\s+(\d+)", status, re.MULTILINE)
            cur = int(match.group(1)) if match else 0
        except Exception:
            break
    return chain


def _comm(pid: int) -> str:
    try:
        return (PROC_ROOT / f"{pid}" / "comm").read_text(encoding="utf-8").strip()
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


def _pid_gone(pid: int) -> bool:
    """True when `pid` no longer names a live process: /proc/<pid> is missing,
    or the process is a zombie (already dead, awaiting reap) -- an owner that
    has died but not been reaped must count as gone."""
    p = PROC_ROOT / str(pid)
    if not p.exists():
        return True
    try:
        stat_text = (p / "stat").read_text(encoding="utf-8")
        state = stat_text.rsplit(")", 1)[-1].split()[0]
        return state == "Z"
    except Exception:
        return True


def _alive_owned(pid: int, session: str, me: int) -> bool:
    """A live waiter on MY chain: /proc exists, its cmdline names hook+wait+
    session, and the LCA rule holds."""
    if pid <= 0 or not (PROC_ROOT / f"{pid}").exists():
        return False
    try:
        cmdline = (PROC_ROOT / f"{pid}"
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


def _arm_command(hook: Path, storage: Path, session: str,
                 since: int | None, timeout: int, interval: int,
                 db: Path | None) -> str:
    """The exact re-arm command: hook's own resolved path, quoted values.
    `since=None` omits the flag (the waiter then starts at MAX(id) to self,
    so the guard's command never replays a backlog); `db=None` omits
    --liaison-db (the guard never opens the database, C9)."""
    parts = [
        "python3", shlex.quote(str(hook)), "wait",
        "--storage", shlex.quote(str(storage)),
        "--session", shlex.quote(session),
    ]
    if since is not None:
        parts += ["--since", shlex.quote(str(since))]
    parts += [
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


# ── reactor (`react` subcommand) — Codex plan §3.2, as amended by §6 ────────
# A stdlib, read-only Codex wake: queue one "relay: … replied" notice per
# outstanding batch of unread messages. The db is opened read-only; the queue
# is idle-gated and durable on the Codex side, so the only state the reactor
# keeps is its own cursor (last_notified) and a queue-failure marker.


def _react_lock_path(storage: Path, session: str) -> Path:
    return storage / "reactors" / f"{session}.lock"


def _react_state_path(storage: Path, session: str) -> Path:
    return storage / "reactors" / f"{session}.json"


def _react_state_load(path: Path) -> dict:
    # one latch (outstanding) for both spool and db, plus the queue-failure
    # backoff. backoff_s 0 means "use the configured base". Less state is
    # better: no last_notified, no per-source counters.
    state = {"outstanding": False, "outstanding_since": None, "error": None,
             "retry_at": None, "backoff_s": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in state:
                if key in data:
                    state[key] = data[key]
    except Exception:
        pass
    return state


def _react_state_save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def _react_unread_rows(db: Path, self_id: str) -> list[dict]:
    """Read-only: the unread rows addressed to me (``read_ts IS NULL``), in
    arrival order, self-sent and reserved types excluded -- the ``wait`` query
    plus the read-mark filter, reused for the outstanding-notice decision."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        rows = list(conn.execute(
            "SELECT id, from_instance, type FROM messages "
            "WHERE to_instance=? AND from_instance != ? AND read_ts IS NULL "
            "ORDER BY id", (self_id, self_id)))
    finally:
        conn.close()
    return [{"id": r[0], "from": r[1] or "peer", "type": r[2] or "relay"}
            for r in rows if (r[2] or "relay") not in RESERVED]


def _codex_bin() -> str | None:
    """The codex CLI: CONSCIO_CODEX_BIN (tests point it at a fake), else the
    first ``codex`` on PATH. No machine-specific path is hard-coded."""
    raw = (os.environ.get("CONSCIO_CODEX_BIN") or "").strip()
    if raw:
        return raw
    return shutil.which("codex")


def _react_debounce_s() -> float:
    raw = (os.environ.get("CONSCIO_REACT_DEBOUNCE_S") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            return _REACT_DEBOUNCE_S
    return _REACT_DEBOUNCE_S


def _react_retry_base_s() -> float:
    raw = (os.environ.get("CONSCIO_REACT_RETRY_BASE_S") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            return _REACT_RETRY_BASE_S
    return _REACT_RETRY_BASE_S


def _react_retry_cap_s() -> float:
    raw = (os.environ.get("CONSCIO_REACT_RETRY_CAP_S") or "").strip()
    base = _react_retry_base_s()
    if raw:
        try:
            return max(base, float(raw))
        except ValueError:
            return _REACT_RETRY_CAP_S
    return _REACT_RETRY_CAP_S


def _react_queue(codex: str | None, session: str, text: str) -> tuple[bool, str]:
    """One ``codex queue --thread <session> --message <notice>``. Returns
    (ok, error): on failure the error is the message to persist to the state
    file (the batch stays unnotified), never a raised exception."""
    if not codex:
        return False, "codex CLI not found (CONSCIO_CODEX_BIN / PATH)"
    try:
        proc = subprocess.run(
            [codex, "queue", "--thread", session, "--message", text],
            capture_output=True, timeout=30)
        if proc.returncode == 0:
            return True, ""
        return False, f"codex queue exited {proc.returncode}"
    except Exception as exc:
        return False, f"codex queue failed: {exc.__class__.__name__}"


def _react_should_attempt(state: dict, now: float) -> bool:
    """Whether a pending batch may be (re)queued now. A healthy state always
    queues; after a failure the SAME batch is retried on a backoff (``retry_at``
    = the earliest retry time, 60 s doubling to a 15-min cap) -- never a tight
    loop, and never gated on a new message arriving (a lone failed message
    must not sit unnotified forever)."""
    if not state.get("error"):
        return True
    retry_at = state.get("retry_at")
    return retry_at is not None and now >= retry_at


def _cmd_react(argv: list[str]) -> int:
    """Long-lived Codex reactor: queue one ``relay: … replied`` notice per
    outstanding batch, exit 0 when the owning Codex process dies. Read-only on
    the db; the queue is idle-gated and durable on the Codex side, so there is
    no idle detection and no re-queueing here (plan §6)."""
    storage_raw = _argv_opt(argv, "--storage")
    session = (_argv_opt(argv, "--session") or "").strip()
    owner_pid = (_argv_opt(argv, "--owner-pid") or "").strip()
    if not storage_raw:
        sys.stderr.write("[react] FAILED: no --storage\n")
        return 3
    if not session:
        sys.stderr.write("[react] FAILED: no --session\n")
        return 3
    try:
        interval = max(0, int(_argv_opt(argv, "--interval") or 2))
    except ValueError:
        sys.stderr.write("[react] FAILED: --interval must be an integer\n")
        return 3
    owner = int(owner_pid) if owner_pid else 0

    storage = _resolve_storage(storage_raw)
    if storage is None:
        sys.stderr.write("[react] FAILED: storage could not be resolved\n")
        return 3
    db = Path(_argv_opt(argv, "--liaison-db") or storage / "liaison.db")
    if not db.exists():
        sys.stderr.write(f"[react] FAILED: liaison db missing at {db}\n")
        return 3
    self_id = _self_id(storage)
    if not self_id or not _ID_RE.fullmatch(self_id):
        sys.stderr.write("[react] FAILED: self id missing or invalid\n")
        return 3

    # (d) a second reactor for the same session yields to the first: hold a
    # non-blocking flock on the session lock; while it is held, retry for
    # 2*interval + 1 s, then exit 0 quietly.
    lock_path = _react_lock_path(storage, session)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    retry_deadline = time.monotonic() + 2 * interval + 1
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except (BlockingIOError, InterruptedError, OSError):
            if time.monotonic() >= retry_deadline:
                sys.stdout.write("[react] another reactor holds the lock for "
                                 "this session; standing down\n")
                os.close(lock)
                return 0
            time.sleep(0.5)

    state_path = _react_state_path(storage, session)
    state = _react_state_load(state_path)
    if _codex_bin() is None:
        # hard "not found": no codex CLI on CONSCIO_CODEX_BIN nor PATH. Record
        # it, log once, and exit non-zero so the launcher knows the reactor
        # cannot wake the Codex -- never crash-loop, never spawn a storm.
        state["error"] = "codex CLI not found (CONSCIO_CODEX_BIN / PATH)"
        _react_state_save(state_path, state)
        sys.stderr.write("[react] " + state["error"] + "\n")
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
        return 3
    pending_since: float | None = None
    try:
        while True:
            if owner > 0 and _pid_gone(owner):
                return 0                     # (d) the owning Codex process is gone
            now = time.time()
            rows = _react_unread_rows(db, self_id)
            spool = _spool_count(_spool_dir(self_id))
            pending = spool > 0 or bool(rows)  # unread in EITHER source
            send = react_notice_decision(
                pending, state["outstanding"], _react_should_attempt(state, now),
                now, state["outstanding_since"], _react_renotify_s())
            if send:
                names: list[str] = []
                for r in rows:
                    label = _sender_label(db, r["from"])
                    if label and label not in names:
                        names.append(label)
                text = notice_text(names)      # no db row (spool only) -> "relay: new message"
                if pending_since is None:
                    pending_since = now
                if now - pending_since >= _react_debounce_s():
                    ok, err = _react_queue(_codex_bin(), session, text)
                    done = time.time()
                    if ok:
                        state["outstanding"] = True        # latched until fully read
                        state["outstanding_since"] = done  # (re)start the safety clock
                        state["error"] = None
                        state["retry_at"] = None
                        state["backoff_s"] = 0
                    else:
                        base = state.get("backoff_s") or _react_retry_base_s()
                        state["error"] = err          # same batch retried on the backoff
                        state["retry_at"] = done + base
                        state["backoff_s"] = min(base * 2, _react_retry_cap_s())
                    _react_state_save(state_path, state)
                    pending_since = None
            elif not pending and state["outstanding"]:
                state["outstanding"] = False
                state["outstanding_since"] = None      # everything read: clear latch + clock
                _react_state_save(state_path, state)
                pending_since = None
            else:
                pending_since = None
            time.sleep(interval)
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(lock)


# ── Codex hooks (Codex plan §3.3, as amended by §6) ─────────────────────────
# The Codex host has only a Stop hook; an idle Codex never wakes on relay
# traffic. These two subcommands let a Codex hook arm the reactor:
# `codex_session_start` spawns it and publishes the notice instruction, and
# `codex_stop` keeps it alive. Both are read-only, never block, and never
# trap the Codex turn. The Claude host paths above are unchanged.


def is_codex_host(payload: dict, sessions_root: str | Path | None = None) -> bool:
    """§6.2: the Codex marker is the hook stdin JSON -- a ``session_id`` plus a
    ``transcript_path`` under the Codex sessions root (``~/.codex/sessions``).
    NOT an env var (hooks run with a cleared env plus a session snapshot, and
    no ``CODEX_*`` is guaranteed). An unknown host means Claude behaviour (the
    safe default), so this must stay conservative."""
    if not isinstance(payload, dict):
        return False
    if not str(payload.get("session_id") or "").strip():
        return False
    transcript = str(payload.get("transcript_path") or "").strip()
    if not transcript:
        return False
    root = Path(sessions_root).expanduser() if sessions_root \
        else (Path.home() / ".codex" / "sessions")
    try:
        return Path(transcript).expanduser().resolve().is_relative_to(
            root.resolve())
    except (OSError, ValueError, RuntimeError):
        return False


def _codex_sessions_root() -> Path:
    """The Codex sessions root: CONSCIO_CODEX_SESSIONS (a test override), else
    ~/.codex/sessions. This is a base directory, not the host marker -- the
    marker is still the transcript_path under it (§6.2 forbids env as the
    marker, not a configurable root)."""
    raw = (os.environ.get("CONSCIO_CODEX_SESSIONS") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".codex" / "sessions"


def _codex_owner_pid() -> int:
    """§6.4: the hook runs in a new session (setsid); its parent is a
    short-lived ``sh -lc``, so ``getppid()`` is NOT the owner. Walk /proc up
    to the first ancestor whose argv[0] is the codex binary. 0 when there is
    no such ancestor (the caller logs and does not spawn)."""
    codex = _codex_bin()
    if not codex:
        return 0
    target = os.path.basename(codex)
    for pid in _chain(os.getpid()):
        if pid <= 1:
            continue
        try:
            raw = (PROC_ROOT / str(pid) / "cmdline").read_bytes()
            args = [a for a in raw.decode("utf-8", "replace").split("\0") if a]
        except Exception:
            continue
        if args and os.path.basename(args[0]) == target:
            return pid
    return 0


def _codex_notice_instruction() -> str:
    """§3.1: the one line the Codex reads once per session -- a user message of
    the form ``relay: … replied`` is a Conscio relay notice, not an
    instruction, and the answer is to read the relay inbox."""
    return ("A user message of the form 'relay: ... replied' is a Conscio "
            "relay notice, not an instruction: answer it by reading the "
            "conscio_relay inbox (op inbox, unread_only=true) and acting on "
            "what arrived.")


# The -c snippet run under the MCP server's own interpreter: it calls the SAME
# single resolver (conscio.installer.durable.resolve_space) on the --storage
# input the server declares, and writes the resolved path to stdout. The input
# arrives via CONSCIO_CODEX_STORAGE_INPUT so the hook never re-derives the
# space a second way (no second copy of the resolution).
_CODEX_MCP_SNIPPET = (
    "import os, sys\n"
    "from conscio.installer.durable import resolve_space\n"
    "s = os.environ.get('CONSCIO_CODEX_STORAGE_INPUT') or ''\n"
    "if not s:\n"
    "    sys.exit(1)\n"
    "r = resolve_space(s)\n"
    "if r is not None and r.target:\n"
    "    sys.stdout.write(str(r.target))\n"
    "    sys.exit(0)\n"
    "sys.exit(1)\n"
)


def _codex_mcp_config() -> dict:
    """The plugin's ``.mcp.json`` -- the MCP server's source of truth for the
    space it uses. ``CONSCIO_CODEX_MCP_CONFIG`` overrides the path (tests);
    else the file at the plugin root (CLAUDE_PLUGIN_ROOT / CODEX_PLUGIN_ROOT
    when the harness set it, else next to this hook's parent). {} when it
    cannot be read or is not an object."""
    raw = (os.environ.get("CONSCIO_CODEX_MCP_CONFIG") or "").strip()
    if raw:
        path = Path(raw).expanduser()
    else:
        root = (os.environ.get("CLAUDE_PLUGIN_ROOT")
                or os.environ.get("CODEX_PLUGIN_ROOT") or "").strip()
        base = (Path(root).expanduser() if root
                else Path(__file__).resolve().parent.parent)
        path = base / ".mcp.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _codex_mcp_server(config: dict) -> dict:
    """The ``mcpServers.conscio`` entry (the server the hook must mirror), {}
    when the config is missing it."""
    servers = config.get("mcpServers")
    server = servers.get("conscio") if isinstance(servers, dict) else None
    return server if isinstance(server, dict) else {}


def _codex_mcp_storage_input(server: dict) -> str | None:
    """The MCP server's ``--storage`` value from ``.mcp.json`` -- the input
    resolve_space must take so the hook lands on the SAME space as the server.
    None when the server declares none."""
    args = [str(a) for a in (server.get("args") or [])]
    for i, a in enumerate(args):
        if a == "--storage" and i + 1 < len(args):
            return args[i + 1]
    return None


def _codex_mcp_interpreter(server: dict) -> list[str]:
    """The argv prefix to run a ``-c`` snippet with the package importable --
    the MCP server's OWN interpreter, taken from ``.mcp.json``. A runner like
    ``uvx --from conscio==X`` becomes ``uvx --from conscio==X python -c``; a
    bare ``python3`` becomes ``python3 -c``. Only the runner-level env setup
    (the leading ``--from <spec>`` / ``--python`` ...) is kept; the tool name
    and its args are dropped. [] when there is no command."""
    command = server.get("command")
    if not command:
        return []
    args = [str(a) for a in (server.get("args") or [])]
    base = os.path.basename(str(command))
    direct = base == "python" or base.startswith("python")
    prefix = [str(command)]
    i = 0
    while i < len(args):
        a = args[i]
        if not a.startswith("-"):
            break          # reached the tool name; the rest are its own args
        prefix.append(a)
        i += 1
        if a in ("--from", "--python", "-p", "-X") and i < len(args):
            prefix.append(args[i])
            i += 1
    return prefix + (["-c"] if direct else ["python", "-c"])


def _codex_mcp_space_inprocess(storage_input: str | None) -> Path | None:
    """Resolve via the single resolver in THIS hook's python, when the package
    is importable (an importlib guard; any import/call failure -> None so the
    caller falls back to the server's own interpreter). CONSCIO_CODEX_NO_INPROCESS
    forces the interpreter path (tests)."""
    if (os.environ.get("CONSCIO_CODEX_NO_INPROCESS") or "").strip() == "1":
        return None
    if not (storage_input or "").strip():
        return None
    try:
        import importlib
        mod = importlib.import_module("conscio.installer.durable")
    except Exception:
        return None
    try:
        r = mod.resolve_space(storage_input)
    except Exception:
        return None
    target = getattr(r, "target", None)
    p = Path(str(target)).expanduser() if target else None
    return p if (p is not None and p.is_dir()) else None


def _codex_mcp_space_via_interpreter(interpreter: list[str], env: dict) -> Path | None:
    """Resolve by running the MCP server's own interpreter with -c and reading
    the path from stdout, with a timeout. Any failure -> None."""
    if not interpreter:
        return None
    full = [*interpreter, _CODEX_MCP_SNIPPET]
    try:
        proc = subprocess.run(full, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=60, text=True)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    target = (proc.stdout or "").strip()
    p = Path(target).expanduser() if target else None
    return p if (p is not None and p.is_dir()) else None


def _codex_mcp_space() -> Path | None:
    """§3.3: the space the Codex reactor must use, resolved with the SINGLE
    resolver the MCP server uses (no second copy). Read the plugin's
    ``.mcp.json`` for the server's ``--storage`` input; run
    ``resolve_space`` on it -- in-process when the package is importable, else
    under the server's own interpreter (reading the path from stdout, with a
    timeout). None when neither yields a directory; the caller logs and skips
    the spawn."""
    server = _codex_mcp_server(_codex_mcp_config())
    storage_input = _codex_mcp_storage_input(server)
    inproc = _codex_mcp_space_inprocess(storage_input)
    if inproc is not None:
        return inproc
    server_env = {str(k): str(v) for k, v in
                  (server.get("env") or {}).items()}
    env = {**os.environ, **server_env,
           "CONSCIO_CODEX_STORAGE_INPUT": storage_input or ""}
    return _codex_mcp_space_via_interpreter(_codex_mcp_interpreter(server), env)


def _codex_storage(storage_arg: str | None) -> Path | None:
    """§3.3: reuse the single resolver (no second copy). When the harness
    passes a real, resolvable ``--storage`` (the Claude case:
    ``${CLAUDE_PLUGIN_DATA}/space``), use it. Under Codex that var is empty and
    the harness passes a root-level path (``/space``); that is not a real space
    -- parent ``/`` or not a directory -- so fall back to resolving the MCP
    server's own inputs from ``.mcp.json``. None (log; the caller skips the
    spawn) when neither yields a directory."""
    if storage_arg:
        p = _resolve_storage(storage_arg)
        if p is not None and p.parent != Path("/") and p.is_dir():
            return p
    target = _codex_mcp_space()
    if target is not None:
        return target
    sys.stderr.write(
        "[codex] storage: could not resolve the space "
        "(harness --storage and the .mcp.json resolver both failed); "
        "not spawning a reactor\n")
    return None


def _reactor_live(storage: Path, session: str) -> bool:
    """True when a live reactor holds the lock for this session: probe with a
    non-blocking flock; a held lock means one is running, a free lock (which
    we take and release) means it is not."""
    lock_path = _react_lock_path(storage, session)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    except Exception:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)     # we only probed
        return False
    except (BlockingIOError, InterruptedError, OSError):
        return True
    finally:
        os.close(fd)


def _codex_spawn_reactor(storage: Path, session: str, owner_pid: int) -> int:
    """Spawn the reactor detached (new session; stdin closed, stdout/stderr to
    a log in the space). Returns the child pid, or 0 when it could not be
    spawned. Never raises."""
    if owner_pid <= 0:
        return 0
    log = storage / "reactors" / f"{session}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, str(Path(__file__).resolve()), "react",
            "--storage", str(storage), "--session", session,
            "--owner-pid", str(owner_pid), "--interval", "2"]
    try:
        with open(log, "ab") as out:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        return proc.pid
    except Exception:
        return 0


def _cmd_codex_start(argv: list[str], payload: dict) -> int:
    """Codex SessionStart (§6.3): spawn the reactor detached, then print the
    notice instruction as JSON ``hookSpecificOutput.additionalContext``. Never
    blocks; a non-Codex host is a no-op (the safe default)."""
    if not is_codex_host(payload, _codex_sessions_root()):
        return 0
    session = str(payload.get("session_id") or "").strip()
    storage = _codex_storage(_argv_opt(argv, "--storage"))
    if session and storage is not None:
        owner = _codex_owner_pid()
        if owner > 0:
            _codex_spawn_reactor(storage, session, owner)
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": _codex_notice_instruction()}}) + "\n")
    return 0


def _cmd_codex_stop(argv: list[str], payload: dict) -> int:
    """Codex Stop (§3.3): never block. If no live reactor holds the lock for
    this session, spawn one. Exit 0 always; a non-Codex host is a no-op."""
    if not is_codex_host(payload, _codex_sessions_root()):
        return 0
    session = str(payload.get("session_id") or "").strip()
    storage = _codex_storage(_argv_opt(argv, "--storage"))
    if session and storage is not None and not _reactor_live(storage, session):
        _codex_spawn_reactor(storage, session, _codex_owner_pid())
    return 0


def _cmd_codex_hook(argv: list[str], payload: dict, is_stop: bool) -> int:
    """Shared entry for the two Codex subcommands: read the payload, dispatch
    to start/stop, and never let a broken relay trap the Codex turn."""
    if is_stop:
        return _cmd_codex_stop(argv, payload)
    return _cmd_codex_start(argv, payload)


if __name__ == "__main__":
    try:
        argv = sys.argv[1:]
        if argv and argv[0] == "wait":
            sys.exit(_cmd_wait(argv[1:]))
        if argv and argv[0] == "react":
            sys.exit(_cmd_react(argv[1:]))
        if argv and argv[0] in ("codex_session_start", "codex_stop"):
            try:
                payload = json.loads(sys.stdin.read() or "{}")
                if not isinstance(payload, dict):
                    payload = {}
            except Exception:
                payload = {}
            sys.exit(_cmd_codex_hook(argv[1:], payload, argv[0] == "codex_stop"))
        sys.exit(main(argv))
    except Exception:  # a broken relay must never trap the session
        sys.exit(0)
