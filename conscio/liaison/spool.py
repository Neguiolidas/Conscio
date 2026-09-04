# conscio/liaison/spool.py
"""Store-and-forward spool — the sender drops a file, the owner ingests it.

The v4.5.4 invariant: every agent reads and writes only its OWN liaison.db;
anyone may drop a file into someone else's spool, but only the owner ingests
its own spool. That is what makes a message to an offline agent work with no
process running — the main case, since a session agent is almost never awake
at the moment the message is sent."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from . import directory, mailbox
from .relay import MAX_PAYLOAD_BYTES

MAX_INGEST_PER_PASS = 500


def deposit(to_id: str, msg: dict) -> str:
    """Write the message into the recipient's spool and return its spool id.

    The path is DERIVED from the validated id, never from a string carried in
    someone else's card (I1). The file becomes visible only under its final
    name: a reader never sees a half-written deposit.
    """
    d = directory.spool_dir(to_id)          # validates the id
    d.mkdir(parents=True, exist_ok=True)
    spool_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}"
    body = json.dumps(msg, ensure_ascii=False)
    tmp = d / f".{spool_id}.tmp"
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, d / f"{spool_id}.json")
    return spool_id


def ingest(db: Path, self_id: str, *, limit: int = MAX_INGEST_PER_PASS) -> int:
    """Ingest the owner's spool; return how many rows actually landed.

    Crash-safe order: INSERT (deduped by spool_id) BEFORE the unlink — dying
    in between re-ingests the file and the unique index drops the duplicate.
    A message that cannot be understood goes to quarantine and its file is
    removed, so one bad drop can never stall the spool.
    """
    try:
        d = directory.spool_dir(self_id)
        files = sorted(p for p in d.glob("*.json") if not p.name.startswith("."))
    except (OSError, ValueError):
        return 0
    n = 0
    for path in files[:limit]:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            inserted = _ingest_one(db, self_id, raw, path.stem)
        except Exception as exc:      # one bad file never stalls the spool
            mailbox.quarantine(db, source_row=0, motivo=f"invalid spool: {exc}",
                               payload_raw=raw[:4096])
            path.unlink(missing_ok=True)
            continue
        path.unlink(missing_ok=True)     # only after the INSERT
        n += 1 if inserted else 0
    return n


def _ingest_one(db: Path, self_id: str, raw: str, spool_id: str) -> bool:
    """Validate one deposited file and insert it. Raises on anything the
    owner should not accept — the caller quarantines and moves on."""
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload above the cap")
    msg = json.loads(raw)
    if not isinstance(msg, dict):
        raise ValueError("message is not an object")
    to = msg.get("to")
    if to not in ("", None, self_id):
        raise ValueError(f"wrong recipient: {to!r}")
    return mailbox.insert_from_spool(
        db, from_instance=str(msg.get("from", "")), to_instance=self_id,
        type=str(msg.get("type", "relay")), payload=msg.get("payload"),
        spool_id=spool_id)
