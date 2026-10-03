# conscio/liaison/relay_transport.py
"""The relay's single exit door: local spool or remote HTTP.

Exactly one address per card — empty ``url`` means a local peer (spool), a
filled ``url`` means a remote peer. No host heuristics: the card decides.

A remote peer that does not answer at its URL is not a dead end: its mail is
parked in this host's spool under its id, the directory a pull bridge serves
to peers that only work as clients (measured 2026-10-03: Jade's card points
at an HTTP port nobody listens on; she long-polls this host's spool instead,
and every send to her failed as "unreachable" while the mailbox she reads
sat one directory away)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import directory, spool
from .relay_net import ACCEPTED, REJECTED, UNREACHABLE, transport_post

# How a message left — the answer of `deliver_route`. Empty means it did not.
VIA_SPOOL = "spool"      # local peer: deposited in its spool
VIA_HTTP = "http"        # remote peer: its bridge accepted it
PARKED = "parked"        # remote peer silent: kept here for it to pull

REMOTES_MODE = 0o600


def remotes_path() -> Path:
    return directory.relay_root() / "remotes.json"


def load_remotes() -> dict[str, dict]:
    """Bearer tokens for remote peers, keyed by instance id. Missing or
    corrupt file reads as "no remotes" — never a crash on the send path."""
    try:
        data = json.loads(remotes_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_remotes(remotes: dict[str, dict]) -> None:
    path = remotes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(remotes, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, REMOTES_MODE)
    os.replace(tmp, path)


def deliver_route(card: dict, msg: dict, *, token: str = "") -> str:
    """How the message left: VIA_SPOOL, VIA_HTTP, PARKED — or "" if it did not.

    A non-empty answer means the message was ACCEPTED somewhere it will be
    read from. The caller must not write its local copy before that: an
    outbox line claiming "sent" with nothing delivered is exactly the lie
    that made the operation unreadable until now (C3).
    """
    cid = str(card.get("instance_id", ""))
    url = str(card.get("url", "")).strip()
    if not directory.valid_id(cid):
        return ""
    if not url:
        # ``spool`` is a MARKER that the peer is local, never a usable path:
        # the directory derives the real path from the validated id (I1). A
        # card with neither address is a peer nobody can reach — say so.
        if not str(card.get("spool", "")).strip():
            return ""
        return VIA_SPOOL if _deposit(cid, msg) else ""
    tk = token or (load_remotes().get(cid, {}) or {}).get("token", "")
    try:
        outcome = transport_post(url, msg, token=tk)
    except Exception:
        outcome = UNREACHABLE
    if outcome == ACCEPTED:
        return VIA_HTTP
    if outcome == REJECTED:
        # The bridge answered and said no (bad token, malformed): that is a
        # misconfiguration to surface, not a silence to paper over.
        return ""
    return PARKED if _deposit(cid, msg) else ""


def deliver(card: dict, msg: dict, *, token: str = "") -> bool:
    """True only when the delivery was ACCEPTED (see `deliver_route`)."""
    return bool(deliver_route(card, msg, token=token))


def _deposit(cid: str, msg: dict) -> bool:
    try:
        spool.deposit(cid, msg)           # path derived from the id (I1)
        return True
    except (OSError, ValueError):
        return False
