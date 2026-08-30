# conscio/liaison/reactor.py
"""agnostic reactive dispatcher (v4.5) — delegate inbound relay msg to agent.

This is the layer that makes the relay *alive*: a persistent loop reads new
peer messages (past the watcher cursor), and for each NON-silent message
runs a notify hook — a subprocess command configured by the environment via
`CONSCIO_NOTIFY_CMD`. The hook points at whatever wakes YOUR agent (e.g.
`hermes send telegram` on Hermes, or a native DM bridge on Sonnet/Gemini).
Universal: the agent is never ignored when a message arrives.

At-least-once delivery:
- cursor only advances past a message whose hook SUCCEEDED (exit 0).
- a failed hook keeps the cursor, so the message re-surfaces next tick.
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
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from . import mailbox
from .watcher import ExitCode, _load_state, _save_state, poll_digest

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
    """One reactive tick: surface & notify new peer messages (at-least-once).

    Returns the number of messages newly delivered to the agent's notify
    hook (or consumed silently). Callers run this in a loop.
    """
    peers = [p for p in peers if p]      # drop empties
    db = Path(db)
    if not self_id or not peers or not db.exists():
        return 0

    # Renew self presence every tick (heartbeat) — the reactor is the live
    # process, so IT keeps the agent visible as live to peers/observatory.
    from . import agents
    agents.register_agent(db, instance_id=self_id,
                          capabilities=("relay",), status="alive")

    def _default(cmd: str, msg: dict) -> bool:
        return run_notify_hook(cmd, msg)
    notify = _notify or _default

    state = _load_state(db)
    new_cursors: dict[str, int] = {}
    delivered = 0

    for peer in peers:
        since = int(state.get(peer, {}).get("last_seen_id", 0))
        msgs = poll_digest(db, since, self_id, [peer])
        if not msgs:
            continue
        cursor = since
        for m in msgs:                 # id order (ASC) per peer
            if not should_notify(m):
                cursor = m["id"]       # consumed silently
                delivered += 1
                continue
            ok = notify(notify_cmd, m)
            if ok:
                cursor = m["id"]       # delivered → claim this id
                delivered += 1
            else:
                # at-least-once: stop advancing here, cursor holds at the
                # last successfully handled id → the failed one re-surfaces.
                break
        if cursor > since:
            new_cursors[peer] = cursor
    if not new_cursors:
        return 0
    _save_state(db, {
        peer: {"last_seen_id": new_cursors[peer], "status": "delivered"}
        for peer in new_cursors
    })
    return delivered


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="conscio reactor",
        description="Reactive relay dispatcher: delegate inbound peer messages"
                    " to the agent's notify hook (CONSCIO_NOTIFY_CMD), loop "
                    "forever, at-least-once.",)
    p.add_argument("--liaison-db", default=None,
                   help="path to liaison.db (default: $HERMES_HOME/liaison.db)")
    p.add_argument("--self-id", default="",
                   help="our provider instance id (or env CONSCIO_SELF_ID)")
    p.add_argument("--relay-peer", action="append", default=[],
                   help="trusted peer id (repeatable)")
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
        n = dispatch(db, self_id=self_id, peers=peers, notify_cmd=notify_cmd)
        if n:
            print(json.dumps({"delivered": n, "ts": time.time()},
                             ensure_ascii=False))
        return n

    if args.once:
        tick()
        return 0
    while True:
        try:
            tick()
        except Exception as exc:
            log.error("tick failed: %s", exc)
        time.sleep(max(args.interval, 0.5))


if __name__ == "__main__":
    raise SystemExit(main())