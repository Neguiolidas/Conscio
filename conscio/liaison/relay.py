# conscio/liaison/relay.py
"""Pure general-relay protocol over the mailbox (v2.6.1).

A free-form directed messaging layer sharing the v2.6.0 mailbox substrate but
disjoint from the review channel: the two review types are reserved (never sent
or surfaced as relay), payloads are capped, read messages are retained for a
bounded window. Pure — validates/filters in Python over mailbox rows; never
touches the DB or the engine."""
from __future__ import annotations

import json

RESERVED_TYPES = {"review_request", "review_verdict"}   # owned by review channel
MAX_PAYLOAD_BYTES = 64 * 1024                            # 65536 (R1)
RETENTION_DAYS = 7                                       # (R2)


def payload_size(payload: object) -> int:
    """Compact-JSON byte size — a storage-independent logical bound."""
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def validate_send(*, to: str, type: str, payload: object,
                  peers: set[str]) -> None:
    """Raise ValueError on any violation; otherwise return None."""
    if not isinstance(type, str) or not type:
        raise ValueError("type must be a non-empty string")
    if type in RESERVED_TYPES:
        raise ValueError(f"type {type!r} is reserved for the review channel")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if to not in peers:
        raise ValueError(f"unknown peer {to!r} (not in --relay-peer allowlist)")
    if payload_size(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")


def is_relay_message(row: dict, peers: set[str]) -> bool:
    """True iff a mailbox row is a surfaceable relay message: from an
    allowlisted peer, non-reserved type, within the size cap."""
    if row.get("from_instance") not in peers:
        return False
    if row.get("type") in RESERVED_TYPES:
        return False
    return payload_size(row.get("payload")) <= MAX_PAYLOAD_BYTES


def envelope_of(row: dict) -> dict | None:
    """The runtime identity embedded in a message's `_meta.from` (v4.5).

    Returns the envelope dict, or None when absent or malformed (a non-dict
    `_meta.from` is ignored — envelope is metadata, it never blocks delivery).
    Mirrors mailbox.send's identity baking (Ato 1)."""
    try:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            return None
        meta = payload.get("_meta")
        if not isinstance(meta, dict):
            return None
        frm = meta.get("from")
        if not isinstance(frm, dict):
            return None
        return frm
    except Exception:
        return None
