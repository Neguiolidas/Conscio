# conscio/liaison/tick.py
"""Dedicated private-cursor relay watcher (v4.3.1).

Unlike ``conscio.liaison.watcher`` (which persists a per-peer cursor in the
shared ``watcher_state`` table that ANY agent writing to the mailbox also
reads/advances), this module keeps an OPT-IN private cursor so that multiple
agents polling the same mailbox never clobber each other's read position.

Scope of this module (pure plumbing, zero LLM):
  * ``sweep()`` — one read-only poll of new self-addressed peer messages
    using a private cursor file (fast, no shared-state writes).
  * ``classify_important()`` — heuristic that tags a surfaced peer message
    as IMPORTANT (direction/action requested) vs routine (ping/validation/
    ack).
  * ``classify_peer()`` — tag the peer a message came from.

Intentionally small and dependency-light (stdlib only), so it can be invoked
as a CLI or imported by host-level supervisor scripts/cron/systemd without
pulling the full Conscio engine.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from . import relay
from .mailbox import default_db

SELF_ID_ENV = "CONSCIO_SELF_ID"

# Message payload fields whose values are scanned for IMPORTANT markers.
# Word-boundary matching where "_" is treated as a word separator too, so we
# match "direcionamento", "DIRECIONAMENTO_PROXIMO_PASSO" but not "validacao"
# (which only CONTAINS "acao" inside "validacao" — no standalone token).
import re as _re

_NONWORD = r"(?![0-9A-Za-z])"
_IMPORTANT_MARKERS = (
    _re.compile(r"(?<![0-9A-Za-z])direcionamento" + _NONWORD, _re.IGNORECASE),
    _re.compile(r"(?<![0-9A-Za-z])acao" + _NONWORD, _re.IGNORECASE),
    _re.compile(r"(?<![0-9A-Za-z])passo" + _NONWORD, _re.IGNORECASE),
    _re.compile(r"(?<![0-9A-Za-z])teste_direcionado" + _NONWORD, _re.IGNORECASE),
    _re.compile(r"(?<![0-9A-Za-z])prioridade" + _NONWORD, _re.IGNORECASE),
)

# Keys the user may sweep for (tipo/status/assunto) — matching real Gemini
# payloads: {"tipo":"direcionamento","status":"DIRECIONAMENTO_PROXIMO_PASSO"}.
_TAGGED_KEYS = ("tipo", "status", "assunto")


def classify_important(payload: dict | str) -> bool:
    """True if a relay message payload requests direction/action.

    Scans ``tipo``/``status``/``assunto`` string values (case-insensitive)
    for direction/action markers. A bare ping/validation/ack with no such
    marker → False.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return False
    if not isinstance(payload, dict):
        return False
    for key in _TAGGED_KEYS:
        val = payload.get(key)
        if isinstance(val, str):
            if any(pat.search(val) for pat in _IMPORTANT_MARKERS):
                return True
    return False


def classify_peer(from_instance: str) -> str:
    """Human label for a relay peer id (or a fallback id token)."""
    m = re.match(r"^([0-9a-f]{8})", from_instance or "")
    return m.group(1) if m else (from_instance or "unknown")[:8]


def sweep(db: Path, self_id: str, peers: list[str],
          cursor_path: Path | None, *, limit: int = 50) -> list[dict]:
    """Read-only sweep for new self-addressed peer messages.

    Uses a PRIVATE cursor file (``cursor_path``) instead of the shared
    ``watcher_state`` table. Each call reads id > cursor, fetches matching
    messages, and returns them WITHOUT advancing the private cursor (the
    caller advances it explicitly so that a consumer that crashes before
    persisting delivery can retry). Missing/corrupt/locked db → [].
    """
    db = Path(db)
    peers = list(dict.fromkeys(p for p in (peers or []) if p))
    if not db.exists() or not self_id or not peers:
        return []
    since = 0
    if cursor_path is not None:
        cp = Path(cursor_path)
        if cp.exists():
            try:
                since = int(cp.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                since = 0
    placeholder = ",".join("?" * len(peers))
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=3000")
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT id, from_instance, to_instance, type, payload, ts"
            " FROM messages"
            " WHERE id > ? AND to_instance = ?"
            f" AND from_instance IN ({placeholder})"
            " ORDER BY id ASC LIMIT ?",
            (since, self_id, *peers, max(1, min(limit, 500))),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    peer_set = set(peers)
    out: list[dict] = []
    for r in rows:
        if r["from_instance"] == self_id:
            continue
        if r["type"] in relay.RESERVED_TYPES:
            continue
        try:
            payload_obj = json.loads(r["payload"])
        except (TypeError, ValueError):
            payload_obj = {"_raw": r["payload"]}
        if not relay.is_relay_message(
            {"from_instance": r["from_instance"], "type": r["type"],
             "payload": payload_obj},
            peer_set,
        ):
            continue
        out.append({
            "id": int(r["id"]),
            "from_instance": r["from_instance"],
            "to_instance": r["to_instance"],
            "type": r["type"],
            "payload": payload_obj,
            "ts": r["ts"],
        })
    return out


def advance_private_cursor(cursor_path: Path, msg_ids: list[int]) -> None:
    """Persist the max message id seen. Best-effort; never raises."""
    if not msg_ids:
        return
    try:
        cp = Path(cursor_path)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(str(max(int(i) for i in msg_ids)), encoding="utf-8")
    except (OSError, ValueError):
        pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="conscio relay sweep",
        description="Sweep the liaison mailbox for new peer messages using a"
                    " private cursor (host supervisor / cron / systemd).",
    )
    p.add_argument("--liaison-db", default=None,
                   help="path to liaison.db (default: $CONSCIO_HOME/liaison.db)")
    p.add_argument("--self-id", default="",
                   help=f"our provider instance id (or env {SELF_ID_ENV})")
    p.add_argument("--relay-peer", action="append", default=[],
                   help="trusted peer id (repeatable)")
    p.add_argument("--cursor", default=None,
                   help="private cursor file path (default: no cursor, always"
                        " swept from id 0)")
    p.add_argument("--always-advance", action="store_true",
                   help="persist the private cursor when messages are"
                        " surfaced (default: leave cursor for caller)")
    p.add_argument("--json", dest="use_json", action="store_true",
                   help="print messages as JSON (default prints the compact"
                        " per-surface summary)")
    args = p.parse_args(argv)

    db = Path(args.liaison_db) if args.liaison_db else default_db()
    self_id = _resolve_self_id(args.self_id)
    peers = list(dict.fromkeys(args.relay_peer or []))

    if not self_id:
        sys.stderr.write(
            f"config error: --self-id or env {SELF_ID_ENV} required\n")
        return 3

    msgs = sweep(db, self_id, peers, Path(args.cursor) if args.cursor else None)

    if not msgs:
        # silent (watchdog) exit — nothing new
        return 0

    if args.use_json:
        print(json.dumps({"self_id": self_id, "messages": msgs},
                         ensure_ascii=False))
    else:
        for m in msgs:
            marker = "IMPORTANT" if classify_important(m["payload"]) else "routine"
            print(f"[{m['id']}] {classify_peer(m['from_instance'])} "
                  f"-> {classify_peer(m['to_instance'])} [{marker}] "
                  f"type={m['type']}")

    if args.always_advance and args.cursor:
        advance_private_cursor(Path(args.cursor),
                               [m["id"] for m in msgs])

    return 0


def _resolve_self_id(arg: str) -> str:
    return (arg or os.environ.get(SELF_ID_ENV, "")).strip()


if __name__ == "__main__":
    raise SystemExit(main())