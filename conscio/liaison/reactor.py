# conscio/liaison/reactor.py
"""agnostic reactive dispatcher (v4.5) — delegate inbound relay msg to agent.

This is the layer that makes the relay *alive*: a persistent loop reads new
peer messages (past the watcher cursor), and for each NON-silent message
runs a notify hook — a subprocess command configured by the environment via
`CONSCIO_NOTIFY_CMD`. The hook points at whatever wakes YOUR agent (e.g.
`hermes send telegram` on Hermes, or a native DM bridge on Sonnet/Gemini).
Universal: the agent is never ignored when a message arrives.

At-least-once delivery:
- a message is marked read only when its hook SUCCEEDED (exit 0).
- a failed hook leaves it unread, so it re-surfaces next tick.
- a message marked silent (`_meta.silent=True` or payload `silent: True`)
  is consumed WITHOUT running the hook (opt-out is the explicit exception).

Pure pipes: engine-free (no conscio.engine import), never raises. The module
gives `should_notify`, `run_notify_hook`, and `dispatch` (one tick), and a
`main()` CLI for the persistent `reactor` loop (systemd-friendly).

The command contract is a single shell command; the message JSON is piped
to its stdin. `CONSCIO_NOTIFY_CMD` may be a full command string or a path.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from . import directory, mailbox, spool
from .watcher import ExitCode

log = logging.getLogger("conscio.liaison.reactor")
NOTIFY_ENV = "CONSCIO_NOTIFY_CMD"
SILENT_KEYS = ("silent", "_silent")   # top-level payload opt-out (compat)


def should_notify(message: dict) -> bool:
    """True unless the message opts out (silent)."""
    payload = message.get("payload") if isinstance(message, dict) else None
    if isinstance(payload, dict):
        # top-level `silent` in payload
        for k in SILENT_KEYS:
            if payload.get(k) is True:
                return False
        # envelope-level `_meta.silent`
        meta = payload.get("_meta")
        if isinstance(meta, dict) and meta.get("silent") is True:
            return False
    return True


def acquire_lock(db: Path, self_id: str) -> int | None:
    """Take the single-reactor lock for this agent; None if someone holds it.

    Two reactors on one mailbox notify every message twice — measured: a
    systemd unit and an in-session thread each delivered all 5 messages of a
    test batch. The window between reading the unread inbox and marking
    `read_ts` is wide enough for both to walk through it.

    flock is the right primitive here: the kernel drops it when the holder
    dies, so an in-session reactor takes over the instant the service stops
    (and hands it back when the service returns). There is no stale lock file
    to reap, which a pid file would have required.
    """
    path = Path(db).parent / f".reactor-{self_id}.lock"
    fd = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:                  # held elsewhere, or the path is unusable
        if fd is not None:
            os.close(fd)
        return None


def release_lock(fd: int | None) -> None:
    """Hand the lock back so another reactor can pick it up immediately."""
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def run_notify_hook(cmd: str, message: dict, *, timeout: float = 15.0) -> bool:
    """Run the notify command, feeding `message` JSON to stdin.

    Returns True only on exit 0. A missing/empty cmd, a nonzero exit, or a
    timeout returns False (never raises) — the message is retried later.
    """
    if not cmd or not message:
        return False
    try:
        data = json.dumps(message, ensure_ascii=False)
        proc = subprocess.run(
            cmd, shell=True, input=data, text=True, capture_output=True,
            timeout=timeout)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired, ValueError):
        log.warning("notify hook failed (msg id=%s)", message.get("id"))
        return False


def dispatch(db: Path, *, self_id: str, peers: Iterable[str],
             notify_cmd: str,
             _notify: Callable[[str, dict], bool] | None = None) -> int:
    """One reactive tick: ingest the spool, notify what is still unread, and
    MARK read_ts on success (at-least-once).

    v4.5.4 killed the per-peer cursor in `watcher_state`. It was a second set
    of books for the same fact — the inbox and `purge_read` never saw it, so a
    message could be "delivered" for the reactor and forever unread for the
    agent (finding A8). `read_ts` is now the only bookkeeping.

    Returns the number of messages newly handed to the notify hook (or
    consumed silently). Callers run this in a loop.
    """
    db = Path(db)
    if not self_id:
        return 0
    try:                        # before the existence guard on purpose: for a
        spool.ingest(db, self_id)   # brand-new agent the spool CREATES the db
    except Exception as exc:
        log.warning("spool ingest failed: %s", exc)
    if not db.exists():
        return 0

    # Renew self presence every tick (heartbeat) — the reactor is the live
    # process, so IT keeps the agent visible as live to peers/observatory.
    from . import agents, relay
    agents.register_agent(db, instance_id=self_id,
                          capabilities=("relay",), status="alive")

    def _default(cmd: str, msg: dict) -> bool:
        return run_notify_hook(cmd, msg)
    notify = _notify or _default

    allow = {p for p in peers if p}      # empty = no restriction (A1)
    delivered = 0
    for row in mailbox.inbox(db, self_id, unread_only=True, limit=200):
        if not relay.is_relay_message(row, allow):
            continue                     # reserved/oversized: left unread for
        if not should_notify(row):       # the tool that owns it
            mailbox.mark_read(db, [row["id"]])      # consumed silently
            delivered += 1
            continue
        if not notify(notify_cmd, row):
            break            # at-least-once: unmarked, it returns next tick
        mailbox.mark_read(db, [row["id"]])
        delivered += 1
    return delivered


class ReactorThread:
    """Reactivity without systemd (C5): it lives inside the MCP server and
    dies with the session — which is exactly when there is nobody left to
    wake. An exception never kills the loop (I8); the last error stays
    visible for `relay_health`."""

    def __init__(self, db: Path, self_id: str, notify_cmd: str,
                 interval: float = 3.0) -> None:
        self.db, self.self_id = Path(db), self_id
        self.notify_cmd, self.interval = notify_cmd, interval
        self.last_error, self.ticks = "", 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock: int | None = None

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run,
                                        name="conscio-reactor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        release_lock(self._lock)          # the service can take over at once
        self._lock = None

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _peers(self) -> list[str]:
        try:
            return [c["instance_id"]
                    for c in directory.peers(exclude=self.self_id)]
        except Exception:                 # a broken directory is not a reason
            return []                     # to stop reacting — empty = accept all

    def _run(self) -> None:
        backoff = self.interval
        while not self._stop.is_set():
            try:
                if self._lock is None:    # a service may already be reacting;
                    self._lock = acquire_lock(self.db, self.self_id)
                if self._lock is not None:
                    dispatch(self.db, self_id=self.self_id,
                             peers=self._peers(),
                             notify_cmd=self.notify_cmd)
                self.ticks += 1           # keep retrying: we inherit the lock
                self.last_error = ""      # the moment that reactor stops
                backoff = self.interval
            except Exception as exc:      # keep looping, but say what broke
                self.last_error = f"{exc}"
                backoff = min(max(backoff * 2, 0.01), 60.0)
            self._stop.wait(backoff)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="conscio reactor",
        description="Reactive relay dispatcher: delegate inbound peer messages"
                    " to the agent's notify hook (CONSCIO_NOTIFY_CMD), loop "
                    "forever, at-least-once.",)
    p.add_argument("--liaison-db", default=None,
                   help="path to liaison.db (default: $CONSCIO_HOME/liaison.db)")
    p.add_argument("--self-id", default="",
                   help="our provider instance id (or env CONSCIO_SELF_ID)")
    p.add_argument("--relay-peer", action="append", default=[],
                   help="restrict to these peer ids (repeatable; default: "
                        "every peer in the directory)")
    p.add_argument("--interval", type=float, default=5.0,
                   help="poll every N seconds (default 5)")
    p.add_argument("--notify-cmd", default=None,
                   help=f"notify hook (default: env {NOTIFY_ENV})")
    p.add_argument("--once", action="store_true",
                   help="single dispatch tick and exit (cron/health mode)")
    args = p.parse_args(argv)

    db = Path(args.liaison_db) if args.liaison_db else mailbox.default_db()
    self_id = os.environ.get("CONSCIO_SELF_ID", "").strip() or args.self_id
    peers = list(dict.fromkeys(args.relay_peer))
    notify_cmd = args.notify_cmd or os.environ.get(NOTIFY_ENV, "").strip()

    if not self_id:
        print("config error: --self-id or CONSCIO_SELF_ID required",
              file=sys.stderr)
        return int(ExitCode.CONFIG_ERROR)
    if not notify_cmd:
        print(f"config error: notify hook required (--notify-cmd or "
              f"{NOTIFY_ENV})", file=sys.stderr)
        return int(ExitCode.CONFIG_ERROR)

    def tick() -> int:
        allow = peers or [c["instance_id"]
                          for c in directory.peers(exclude=self_id)]
        n = dispatch(db, self_id=self_id, peers=allow, notify_cmd=notify_cmd)
        if n:
            print(json.dumps({"delivered": n, "ts": time.time()},
                             ensure_ascii=False))
        return n

    # One reactor per agent notifies; the others idle until it lets go.
    lock = acquire_lock(db, self_id)
    if args.once:
        if lock is None:
            print("another reactor holds this mailbox", file=sys.stderr)
            return 0
        tick()
        release_lock(lock)
        return 0
    while True:
        try:
            if lock is None:
                lock = acquire_lock(db, self_id)
            if lock is not None:
                tick()
        except Exception as exc:
            log.error("tick failed: %s", exc)
        time.sleep(max(args.interval, 0.5))


if __name__ == "__main__":
    raise SystemExit(main())