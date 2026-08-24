# conscio/liaison/a2a.py
"""Capability-based routing over the shared liaison mailbox (v4.1.1, Ato 2).

Lets an emitter select `to_instance` by capability tag instead of a
hardcoded peer list. Backed by `agents.list_agents(..., capability=...)`
and a stable, deterministic choice (alphabetical, deterministic tiebreak).

Pure plumbing — no LLM, never raises (missing/corrupt/locked db degrades
to [])). Designed to be composable with `mailbox.send` and the watcher.
"""
from __future__ import annotations

from . import agents, mailbox


def candidates_by_capability(db, capability: str) -> list[str]:
    """All alive agents carrying the given capability tag (instance_ids)."""
    if not capability or not isinstance(capability, str):
        return []
    return [a["instance_id"] for a in agents.list_agents(
        db, capability=capability, include_stale=False,
    )]


def route_select(db, capability: str, *, prefer: str | None = None) -> str:
    """Pick one `to_instance` for a given capability.

    Resolution order:
      1. `prefer` if it carries the capability and is alive.
      2. The alphabetically smallest alive peer carrying the capability
         (deterministic — same inputs always yield the same choice).

    Returns "" when no candidate exists (caller decides whether to fall
    back to a hardcoded peer or surface the failure).
    """
    if not capability or not isinstance(capability, str):
        return ""
    cap = capability.strip().lower()
    if not cap:
        return ""
    if prefer and agents.is_alive(db, prefer):
        a = agents.get_agent(db, prefer)
        if a and cap in a.get("capabilities", []):
            return prefer
    candidates = sorted(candidates_by_capability(db, cap))
    return candidates[0] if candidates else ""


def route_and_send(db, *, from_instance: str, capability: str,
                   type: str, payload: dict,
                   prefer: str | None = None) -> int:
    """Resolve a capability to an instance_id, then `mailbox.send`. Returns
    the new message id (0 on resolution failure). Pure plumbing: never
    raises. Caller must pre-validate `payload` (size cap) via relay."""
    if not capability or not from_instance:
        return 0
    target = route_select(db, capability, prefer=prefer)
    if not target:
        return 0
    return mailbox.send(
        db, from_instance=from_instance, to_instance=target,
        type=type, payload=payload,
    )


def delta_ack_for(instance_id: str, last_seen_id: int) -> dict:
    """Build a deterministic acknowledgement payload (the A2A ACK wire format).

    The receiver stamps this onto its outbound reply's payload so the
    emitter can advance its cursor without re-fetching the full thread.
    Pure: no I/O. Wire-stable: {ack: {to, since_id}}."""
    return {"ack": {"to": instance_id, "since_id": int(last_seen_id)}}
