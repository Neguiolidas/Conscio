# conscio/agency/relay_cognize.py
"""Cognition-routed relay auto-responder (v2.9.0 "Mind in the loop", Relay
Phase 3 slice 2). Like relay_respond.auto_respond, but the reply is generated
through the engine's READ-ONLY cognition — identity (get_state_for_injection),
memory (recall; peer text is the QUERY only), and advisory (coherence/goals/
status) — not a thin raw-adapter fork.

Integrity boundary (B1): peer text is NEVER written to episodic memory / the
world-model / goals. This module calls ONLY the engine read-trio
(get_state_for_injection / recall / advisory) — never perceive / reflect / run /
remember. It takes the engine as an argument and does NOT import conscio.engine;
this is AGENCY (which may use the engine), so the liaison engine-free invariant
is untouched. Loop-breaker, _fit cap, per-row mark_read all match relay_respond.
"""
from __future__ import annotations

from ..liaison import mailbox, relay
from .relay_respond import _fit, _msg_text, _transcript

DEFAULT_SYSTEM = (
    "You are an autonomous agent replying to a peer agent over a relay "
    "channel, speaking from your own mind. Draw on your self-state, your "
    "recalled memory, and the conversation to reply concisely and in "
    "character. Reply with prose only.")


def _advisory_line(adv) -> str:
    """One compact machine-signal line from advisory(): coherence + goal count +
    status flags. Empty string for a non-dict / empty advisory."""
    if not isinstance(adv, dict):
        return ""
    coh = adv.get("coherence") or {}
    goals = adv.get("goals") or []
    status = adv.get("status") or {}
    bits: list[str] = []
    if isinstance(coh, dict) and coh.get("score") is not None:
        dom = coh.get("dominant")
        bits.append(f"coherence={coh['score']}" + (f" ({dom})" if dom else ""))
    bits.append(f"active goals: {len(goals)}")
    if isinstance(status, dict):
        if status.get("action_lockdown"):
            bits.append("action lockdown")
        if status.get("brake"):
            bits.append(str(status["brake"]))
    return "Cognitive state: " + "; ".join(bits)


def _mind_block(engine, query: str, *, recall_k: int) -> str:
    """Read-only cognition context: identity/state + recalled memory + advisory.
    Calls ONLY the engine read-trio. Every sub-call is defensive — a failing
    surface degrades that section to empty, never breaks the reply."""
    parts: list[str] = []
    try:
        ident = engine.get_state_for_injection()
    except Exception:
        ident = ""
    if ident:
        parts.append("You are:\n" + ident)
    try:
        snippets = engine.recall(query, recall_k) if query else []
    except Exception:
        snippets = []
    if snippets:
        parts.append("Relevant memory:\n"
                     + "\n".join(f"- {s}" for s in snippets))
    try:
        adv = engine.advisory()
    except Exception:
        adv = None
    line = _advisory_line(adv)
    if line:
        parts.append(line)
    return "\n\n".join(parts)


def cognize_respond(engine, adapter, liaison_db, self_id, peers, *,
                    limit: int = 10, max_reply_tokens: int = 512,
                    thread_limit: int = 20, max_prompt_chars: int = 8000,
                    recall_k: int = 3, max_reply_chars: int = 2000,
                    system: str = DEFAULT_SYSTEM) -> list[dict]:
    """Auto-reply to unread peer relay messages, routed through engine cognition.
    Returns sent packets. No-op ([]) when engine/adapter is None, liaison_db/
    self_id falsy, or peers empty."""
    if (engine is None or adapter is None or not liaison_db or not self_id
            or not peers):
        return []
    peers = set(peers)
    inbox_rows = mailbox.inbox(liaison_db, self_id, types=None,
                               unread_only=True, limit=limit)   # newest-first
    sent: list[dict] = []
    for m in inbox_rows:
        typ = m.get("type", "")
        frm = m.get("from_instance", "")
        payload = m.get("payload")
        if typ in relay.RESERVED_TYPES:
            continue                                     # review channel owns it
        if not relay.is_relay_message(m, peers):
            continue                                     # non-peer/oversized
        if isinstance(payload, dict) and payload.get("auto_reply") is True:
            mailbox.mark_read(liaison_db, [m["id"]])     # R2: consume, not answer
            continue
        peer_text = _msg_text(payload)
        thread_rows = mailbox.thread(liaison_db, self_id, frm,
                                     limit=thread_limit)
        transcript = _transcript(thread_rows, self_id, max_prompt_chars)
        mind = _mind_block(engine, peer_text, recall_k=recall_k)
        prompt = (system + "\n\n" + mind
                  + "\n\nConversation so far:\n" + transcript)
        try:
            text = adapter.generate(prompt, max_tokens=max_reply_tokens).text
        except Exception:                                # fork 4: leave UNREAD
            continue
        if len(text) > max_reply_chars:                  # ressalva: cap pre-_fit
            text = text[:max_reply_chars]
        reply = _fit({"text": text, "auto_reply": True,
                      "in_reply_to": m["id"]})
        rid = mailbox.send(liaison_db, from_instance=self_id, to_instance=frm,
                           type=typ, payload=reply)      # type echoed
        mailbox.mark_read(liaison_db, [m["id"]])         # R2: per-row, post-send
        sent.append({"to": frm, "in_reply_to": m["id"], "reply_id": rid})
    return sent
