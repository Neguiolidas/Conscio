# conscio/agency/relay_initiate.py
"""Proactive cognition-routed relay initiation (v2.10.0 "Initiative", Relay
Phase 3). The Awake daemon can OPEN a directed conversation with a peer, or
BROADCAST an announcement to all peers, generated through the engine's READ-ONLY
cognition (identity / memory / advisory) — the proactive counterpart to
relay_cognize's reactive responder.

Integrity boundary: like relay_cognize, this calls ONLY the engine read-trio
(get_state_for_injection / recall / advisory) — never perceive / reflect / run /
remember. Proactive initiation does NOT write episodic memory. It takes the
engine as an argument and does NOT import conscio.engine; this is AGENCY, so the
liaison engine-free invariant is untouched.

Safety gates: advisory lockdown/brake -> fail-closed suppress (stage 1, no
adapter call); NOTHING/empty -> suppress; directed no-storm (don't re-open while
awaiting a reply); broadcast outstanding-guard (don't re-broadcast with zero peer
engagement). Cadence + runtime-awake gating live in the daemon caller.
"""
from __future__ import annotations

from ..liaison import mailbox
from .relay_cognize import _mind_block
from .relay_respond import _fit, _msg_text, _transcript

DEFAULT_INITIATE_SYSTEM = (
    "You are an autonomous agent that may proactively reach out to a peer agent "
    "over a relay channel, speaking from your own mind. Only speak when you "
    "genuinely have something worth saying.")

_DIRECTED_SALIENCE = (
    "\n\nIf there is something you genuinely need to say to {peer} right now, "
    "say it concisely in character. If there is nothing worth saying, reply with "
    "exactly: NOTHING")

_BROADCAST_SALIENCE = (
    "\n\nIf there is something worth announcing to all your peers right now, say "
    "it concisely. If there is nothing worth announcing, reply with exactly: "
    "NOTHING")


def _suppressed(text: str) -> bool:
    t = (text or "").strip()
    return not t or t.upper() == "NOTHING"


def _blocked_by_state(engine) -> bool:
    """Stage-1 cheap gate (no adapter call): True (suppress) if advisory is
    unavailable / malformed, or signals action_lockdown / a tripped brake.
    FAIL-CLOSED — any error suppresses initiation."""
    try:
        adv = engine.advisory()
    except Exception:
        return True
    if not isinstance(adv, dict):
        return True
    status = adv.get("status") or {}
    if not isinstance(status, dict):
        return False
    return bool(status.get("action_lockdown") or status.get("brake"))


def initiate(engine, adapter, liaison_db, self_id, peers, *,
             broadcast: bool = False, recall_k: int = 3,
             thread_limit: int = 20, max_prompt_chars: int = 8000,
             max_reply_tokens: int = 512, max_reply_chars: int = 2000,
             system: str = DEFAULT_INITIATE_SYSTEM) -> list[dict]:
    """Proactively initiate relay messages through read-only cognition. Returns
    sent packets. No-op ([]) when engine/adapter is None, liaison_db/self_id
    falsy, peers empty, or a stage-1 safety gate suppresses."""
    if (engine is None or adapter is None or not liaison_db or not self_id
            or not peers):
        return []
    if _blocked_by_state(engine):                        # gate 6 (fail-closed)
        return []
    peers = list(peers)
    if broadcast:
        return _broadcast(engine, adapter, liaison_db, self_id, peers,
                          recall_k=recall_k, max_reply_tokens=max_reply_tokens,
                          max_reply_chars=max_reply_chars, system=system)
    return _directed(engine, adapter, liaison_db, self_id, peers,
                     recall_k=recall_k, thread_limit=thread_limit,
                     max_prompt_chars=max_prompt_chars,
                     max_reply_tokens=max_reply_tokens,
                     max_reply_chars=max_reply_chars, system=system)


def _directed(engine, adapter, liaison_db, self_id, peers, *, recall_k,
              thread_limit, max_prompt_chars, max_reply_tokens, max_reply_chars,
              system) -> list[dict]:
    sent: list[dict] = []
    for peer in peers:
        thread = mailbox.thread(liaison_db, self_id, peer, limit=thread_limit)
        if thread and thread[-1].get("from_instance") == self_id:
            continue                                     # gate 4: awaiting reply
        query = ""
        for m in reversed(thread):
            if m.get("from_instance") == peer:
                query = _msg_text(m.get("payload"))
                break
        mind = _mind_block(engine, query, recall_k=recall_k)
        transcript = _transcript(thread, self_id, max_prompt_chars)
        prompt = (system + "\n\n" + mind + "\n\nConversation so far:\n"
                  + transcript + _DIRECTED_SALIENCE.format(peer=peer[:12]))
        try:
            text = adapter.generate(prompt, max_tokens=max_reply_tokens).text
        except Exception:                                # leave for next cadence
            continue
        if _suppressed(text):                            # gate 5
            continue
        if len(text) > max_reply_chars:
            text = text[:max_reply_chars]
        payload = _fit({"text": text, "initiated": True})
        rid = mailbox.send(liaison_db, from_instance=self_id, to_instance=peer,
                           type="chat", payload=payload)
        sent.append({"to": peer, "reply_id": rid, "mode": "directed"})
    return sent


def _broadcast(engine, adapter, liaison_db, self_id, peers, *, recall_k,
               max_reply_tokens, max_reply_chars, system) -> list[dict]:
    lb = mailbox.last_broadcast_ts(liaison_db, self_id)
    if lb is not None:                                   # gate 4' outstanding-guard
        rows = mailbox.inbox(liaison_db, self_id, unread_only=False, limit=50)
        if not any(float(r.get("ts", 0.0)) > lb for r in rows):
            return []                                    # no engagement since
    mind = _mind_block(engine, "", recall_k=recall_k)
    prompt = system + "\n\n" + mind + _BROADCAST_SALIENCE
    try:
        text = adapter.generate(prompt, max_tokens=max_reply_tokens).text
    except Exception:
        return []
    if _suppressed(text):
        return []
    if len(text) > max_reply_chars:
        text = text[:max_reply_chars]
    payload = _fit({"text": text, "initiated": True, "broadcast": True})
    sent: list[dict] = []
    for peer in peers:
        try:
            rid = mailbox.send(liaison_db, from_instance=self_id,
                               to_instance=peer, type="chat", payload=payload)
        except Exception:
            continue
        sent.append({"to": peer, "reply_id": rid, "mode": "broadcast"})
    return sent
