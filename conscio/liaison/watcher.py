# conscio/liaison/watcher.py
"""Native A2A relay watchdog over the shared mailbox (v4.1.1).

Replaces the external relay_watch_hermes.py. Reads liaison.db read-only,
deposits new peer messages to an outbox JSON (the handoff the main session
consumes), persists a per-peer cursor in watcher_state inside the same db.
Zero LLM: pure deterministic plumbing.

Exit codes: 0 = ok (incl. new messages surfaced via stdout/outbox);
2 = emit-to-outbox failed (pending_capture — retry next tick);
3 = config error (no db / no self-id / no peers).

Never raises: missing/corrupt/locked db degrades to [] (RelaySensor rule).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterable
from enum import IntEnum
from pathlib import Path

from . import relay
from .mailbox import default_db

BUSY_TIMEOUT_MS = 3000  # mirror relay_watch_hermes.py
STATE_TABLE = "watcher_state"
OUTBOX_NAME = "relay_inbox.json"
SELF_ID_ENV = "CONSCIO_SELF_ID"


class ExitCode(IntEnum):
    OK = 0
    PENDING_CAPTURE = 2  # emit-to-outbox failed; retry next tick
    CONFIG_ERROR = 3    # missing db / self-id / peers


# ── watcher_state schema ──────────────────────────────────────────────
# Per-peer cursor (the right granularity: peer A dropping out must not
# freeze peer B's progress). PRIMARY KEY = peer id.
_STATE_DDL = (
    f"CREATE TABLE IF NOT EXISTS {STATE_TABLE} ("
    " peer         TEXT PRIMARY KEY,"
    " last_seen_id INTEGER NOT NULL DEFAULT 0,"
    " status       TEXT NOT NULL DEFAULT 'idle'"
    ")"
)
_STATE_COLS = ("peer", "last_seen_id", "status")


def _state_conn(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute(_STATE_DDL)
    conn.commit()
    return conn


def _load_state(db: Path) -> dict[str, dict]:
    """All watcher_state rows keyed by peer. Missing/broken db → {}."""
    db = Path(db)
    if not db.exists():
        return {}
    try:
        conn = _state_conn(db)
        try:
            rows = conn.execute(
                f"SELECT {_STATE_COLS[0]}, {_STATE_COLS[1]}, {_STATE_COLS[2]}"
                f" FROM {STATE_TABLE}"
            ).fetchall()
            return {r["peer"]: {"last_seen_id": int(r["last_seen_id"]),
                                "status": r["status"]} for r in rows}
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


def _save_state(db: Path, updates: dict[str, dict]) -> None:
    """UPSERT per-peer state rows. Never raises (best-effort)."""
    if not updates:
        return
    try:
        conn = _state_conn(Path(db))
        try:
            for peer, st in updates.items():
                conn.execute(
                    f"INSERT INTO {STATE_TABLE}(peer,last_seen_id,status)"
                    " VALUES(?,?,?)"
                    " ON CONFLICT(peer) DO UPDATE SET"
                    "   last_seen_id=excluded.last_seen_id,"
                    "   status=excluded.status",
                    (str(peer), int(st["last_seen_id"]), str(st["status"])),
                )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _read_since(db: Path, peer: str) -> int:
    """Per-peer last_seen_id; 0 if unknown."""
    return int(_load_state(db).get(peer, {}).get("last_seen_id", 0))


# ── Poll ──────────────────────────────────────────────────────────────

def poll_digest(db: Path, since_id: int, self_id: str,
                peers: Iterable[str], *, limit: int = 100) -> list[dict]:
    """New self-addressed peer messages in chronological order.

    Filters: id > since_id, to_instance = self_id, from_instance ∈ peers,
    non-reserved type, payload within size cap. Own messages (self_id)
    are skipped. Missing/corrupt/locked db → [].
    """
    db = Path(db)
    peers = list(peers)
    if not peers or not self_id:
        return []
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    except sqlite3.Error:
        return []
    try:
        placeholders = ",".join("?" * len(peers))
        rows = conn.execute(
            "SELECT id, from_instance, to_instance, type, payload, ts"
            " FROM messages"
            " WHERE id > ? AND to_instance = ?"
            f"   AND from_instance IN ({placeholders})"
            " ORDER BY id ASC LIMIT ?",
            (since_id, self_id, *peers, max(1, min(limit, 500))),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    peer_set = set(peers)
    out: list[dict] = []
    for r in rows:
        frm = r["from_instance"]
        if frm == self_id:
            continue
        if r["type"] in relay.RESERVED_TYPES:
            continue
        try:
            payload_obj = json.loads(r["payload"])
        except (TypeError, ValueError):
            payload_obj = {"_raw": r["payload"]}
        if not relay.is_relay_message(
            {"from_instance": frm, "type": r["type"], "payload": payload_obj},
            peer_set,
        ):
            continue
        out.append({
            "id": int(r["id"]),
            "from_instance": frm,
            "to_instance": r["to_instance"],
            "type": r["type"],
            "payload": payload_obj,
            "ts": r["ts"],
        })
    return out


# ── Tick ──────────────────────────────────────────────────────────────

def tick_once(db: Path, *, self_id: str, peers: list[str],
              outbox: Path | None) -> tuple[list[dict], ExitCode]:
    """One poll turn per peer. Read cursor → poll → emit outbox → advance.

    Silent-when-empty: no msgs → outbox untouched, cursors unchanged, OK.
    """
    db = Path(db)
    if not db.exists() or not peers or not self_id:
        return [], ExitCode.CONFIG_ERROR

    state = _load_state(db)
    per_peer: dict[str, list[dict]] = {}
    new_cursors: dict[str, int] = {}

    for peer in peers:
        since = int(state.get(peer, {}).get("last_seen_id", 0))
        msgs = poll_digest(db, since, self_id, [peer])
        if msgs:
            per_peer[peer] = msgs
            new_cursors[peer] = max(m["id"] for m in msgs)

    if not new_cursors:
        return [], ExitCode.OK

    # Advance cursors only after a successful outbox write below.
    pending = {
        peer: {"last_seen_id": new_cursors[peer], "status": "pending_consumption"}
        for peer in new_cursors
    }

    if outbox is None:
        # NO outbox: stdout IS the durable delivery (the cron feeds it to
        # the consumer channel, e.g. Telegram). So we print each peer's
        # new messages AND advance the cursor — exactly the legacy
        # relay_watch_hermes.py contract. Re-surfacing the same set on the
        # next tick would spam the channel, so not advancing is wrong here.
        for peer, msgs in per_peer.items():
            print(json.dumps({"peer": peer, "messages": msgs},
                             ensure_ascii=False))
        _save_state(db, pending)
        return [m for msgs in per_peer.values() for m in msgs], ExitCode.OK

    outbox = Path(outbox)
    try:
        outbox.parent.mkdir(parents=True, exist_ok=True)
        tmp = outbox.with_suffix(outbox.suffix + ".new")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"status": "pending_consumption",
                       "self_id": self_id,
                       "messages_by_peer": per_peer}, f, ensure_ascii=False)
        os.replace(tmp, outbox)
    except OSError:
        # Emit failed: leave cursors where they were, mark pending_capture.
        _save_state(db, {peer: {"last_seen_id": int(state.get(
            peer, {}).get("last_seen_id", 0)), "status": "pending_capture"}
            for peer in per_peer})
        return [m for msgs in per_peer.values() for m in msgs], ExitCode.PENDING_CAPTURE

    # Emit succeeded: advance cursors.
    _save_state(db, pending)
    return [m for msgs in per_peer.values() for m in msgs], ExitCode.OK


def tick_summary(db: Path, *, self_id: str, peers: list[str],
                 outbox: Path | None) -> dict:
    """Reactive tick contract (v4.5): a 3-state STRUCTURED summary, never
    silence — `nada_novo` | `entregue` | `não_entregue` (+ motivo). Also
    renews the agent's presence row (`agents`) so the watcher is both the
    mail-poll AND the liveness heartbeat.

    Returns a dict with keys: estado, motivo, cursor, par, ts, messages.
    `messages` present only when estado == "entregue" (keeps the legacy
    stdout contract). Never raises — any error degrades to "não_entregue"
    with a motivo instead of crashing the tick.
    """
    from . import agents  # local import: watcher stays engine-free but may
    #                       read/write the agents registry (pure sqlite).
    ts = time.time()

    # Liveness: register self on first sight, refresh heartbeat every tick.
    # Best-effort — a failing registry never aborts the actual mail poll.
    if self_id:
        agents.register_agent(db, instance_id=self_id,
                              capabilities=("relay",), status="alive")
    # (heartbeat happens implicitly on the next register UPSERT; explicit
    #  `heartbeat` call is optional — register already refreshes the row.)

    msgs: list[dict] = []
    cursor: dict[str, int] = {}
    try:
        state = _load_state(db)
        cursor = {p: int(state.get(p, {}).get("last_seen_id", 0))
                  for p in peers}
        if not Path(db).exists() or not peers or not self_id:
            return {"estado": "não_entregue", "motivo": "config: db, peers, self_id",
                    "cursor": cursor, "par": self_id, "ts": ts, "messages": []}
        msgs, code = tick_once(db, self_id=self_id, peers=peers, outbox=outbox)
    except Exception as exc:
        return {"estado": "não_entregue", "motivo": f"exceção: {type(exc).__name__}",
                "cursor": cursor, "par": self_id, "ts": ts, "messages": []}

    if code == ExitCode.CONFIG_ERROR:
        estado, motivo = "não_entregue", "config: db ausente ou peers/self_id"
    elif code == ExitCode.PENDING_CAPTURE:
        estado, motivo = "não_entregue", "pending_capture: emit do outbox falhou"
    elif msgs:
        estado, motivo = "entregue", ""
    else:
        estado, motivo = "nada_novo", ""

    # refresh cursor do estado mais novo lido
    if msgs:
        cursor = {p: max((m["id"] for m in msgs if m["from_instance"] == p),
                         default=cursor.get(p, 0)) for p in peers}
    return {"estado": estado, "motivo": motivo, "cursor": cursor,
            "par": self_id, "ts": ts, "messages": msgs}


# ── CLI ───────────────────────────────────────────────────────────────

def _resolve_self_id(arg: str) -> str:
    if arg:
        return arg
    env = os.environ.get(SELF_ID_ENV, "").strip()
    return env


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="conscio relay watcher",
        description="Watchdog over the liaison mailbox: surface new inbound"
                    " peer messages to the outbox, persist a per-peer cursor,"
                    " exit silently when idle.",
    )
    p.add_argument("--liaison-db", default=None,
                   help="path to liaison.db (default: $CONSCIO_HOME/liaison.db)")
    p.add_argument("--self-id", default="",
                   help=f"our provider instance id (or env {SELF_ID_ENV})")
    p.add_argument("--relay-peer", action="append", default=[],
                   help="trusted peer id (repeatable)")
    p.add_argument("--outbox", default=None,
                   help="write new messages as JSON here")
    p.add_argument("--once", action="store_true",
                   help="single poll tick and exit (cron mode)")
    p.add_argument("--since", type=int, default=None,
                   help="override the per-peer cursor start (legacy compat:"
                        " the old relay_watch_hermes --since)")
    p.add_argument("--interval", type=float, default=0.0,
                   help="when >0 and not --once, poll in a persistent loop"
                        " sleeping this many seconds between ticks")
    p.add_argument("--timeout", type=float, default=600.0,
                   help="with --interval, max seconds the loop may run before"
                        " exiting (default 600; defaults to one-shot otherwise)")
    args = p.parse_args(argv)

    db = Path(args.liaison_db) if args.liaison_db else default_db()
    self_id = _resolve_self_id(args.self_id)
    peers = list(dict.fromkeys(args.relay_peer))  # preserve order, dedupe

    if not self_id:
        sys.stderr.write(
            f"config error: --self-id or env {SELF_ID_ENV} required\n")
        return int(ExitCode.CONFIG_ERROR)

    # Legacy --since override: force the cursor back to this id for all peers
    # so the previous processed message becomes the boundary again. This
    # mirrors relay_watch_hermes.py --since (a replay/recover hook).
    if args.since is not None and db.exists():
        _app_state = _load_state(db)
        _app_state.update({
            str(p): {"last_seen_id": int(args.since),
                     "status": _app_state.get(str(p), {}).get(
                         "status", "idle")}
            for p in peers
        })
        _save_state(db, _app_state)

    # Persistent loop (legacy blocking-watcher parity + v4.5 reativo):
    # polls every --interval calling tick_summary (3-state, renova presença).
    # Emite heartbeat "vivo" mesmo sem msgs novas — transforma "não recebi
    # nada" de ambíguo em diagnóstico. Exits OK when a message surfaces or
    # the deadline elapses (silent-idle contract preserved).
    if not args.once and args.interval > 0:
        import time as _time
        deadline = _time.time() + max(args.timeout, 0.0)
        outbox = Path(args.outbox) if args.outbox else None
        while _time.time() < deadline:
            s = tick_summary(db, self_id=self_id, peers=peers, outbox=outbox)
            if s["estado"] == "entregue" and s["messages"]:
                # surfaced — print (stdout contract) and exit OK
                print(json.dumps({"messages": s["messages"]}, ensure_ascii=False))
                return int(ExitCode.OK)
            if s["estado"] == "não_entregue" and s["motivo"].startswith("config"):
                # db/peers/self_id inválido de verdade — honest config error,
                # retry-able invocação, não spin forever
                return int(ExitCode.CONFIG_ERROR)
            # heartbeat "vivo" (supervisor/systemd pode ler o diagnóstico)
            print(json.dumps({"estado": s["estado"], "cursor": s["cursor"],
                              "par": self_id, "ts": s["ts"]}, ensure_ascii=False))
            _time.sleep(args.interval)
        # deadline reached: silent (watchdog) exit OK
        return int(ExitCode.OK)

    _, code = tick_once(
        db, self_id=self_id, peers=peers,
        outbox=Path(args.outbox) if args.outbox else None,
    )
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
