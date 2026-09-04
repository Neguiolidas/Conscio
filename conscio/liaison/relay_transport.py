# conscio/liaison/relay_transport.py
"""The relay's single exit door: local spool or remote HTTP.

Exactly one address per card — empty ``url`` means a local peer (spool), a
filled ``url`` means a remote peer. No host heuristics: the card decides."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import directory, spool
from .relay_net import transport_send

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


def deliver(card: dict, msg: dict, *, token: str = "") -> bool:
    """True only when the delivery was ACCEPTED.

    The caller must not write its local copy before this returns true: an
    outbox line claiming "sent" with nothing delivered is exactly the lie
    that made the operation unreadable until now (C3).
    """
    cid = str(card.get("instance_id", ""))
    url = str(card.get("url", "")).strip()
    if not directory.valid_id(cid):
        return False
    if not url:
        # ``spool`` is a MARKER that the peer is local, never a usable path:
        # the directory derives the real path from the validated id (I1). A
        # card with neither address is a peer nobody can reach — say so.
        if not str(card.get("spool", "")).strip():
            return False
        try:
            spool.deposit(cid, msg)       # path derived from the id (I1)
            return True
        except (OSError, ValueError):
            return False
    tk = token or (load_remotes().get(cid, {}) or {}).get("token", "")
    try:
        return bool(transport_send(url, msg, token=tk))
    except Exception:
        return False
