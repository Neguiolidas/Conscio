# conscio/agency/relay_respond.py
"""Pure relay auto-responder — the daemon's awake auto-reply path (v2.7.0
"Phase 2"). Reads unread free-form relay messages from allowlisted peers,
generates one reply each via a raw InferenceAdapter, and sends it back over the
mailbox. An agency->liaison bridge: imports nothing from conscio.engine, so the
reply is a thin adapter call (no engine memory/advisory). Loop-bounded: own
replies carry payload.auto_reply=True and such inbound is consumed, never
re-answered. mark_read is per-row, immediately after a successful send / skip."""
from __future__ import annotations

import json

from ..liaison import mailbox, relay

DEFAULT_SYSTEM = (
    "You are an autonomous agent replying to a peer agent over a relay "
    "channel. Read the peer's message and reply concisely and helpfully. "
    "Reply with prose only.")


def _msg_text(payload) -> str:
    """Best-effort human text from a relay payload; falls back to compact JSON."""
    if isinstance(payload, dict):
        for k in ("text", "body", "message"):
            v = payload.get(k)
            if isinstance(v, str) and v:
                return v
    return json.dumps(payload, separators=(",", ":"))


def _is_auto_reply(payload) -> bool:
    """The loop-breaker marker — a machine-generated auto reply's payload.
    Single definition; relay_cognize/relay_initiate import it (the marker is a
    cross-module contract)."""
    return isinstance(payload, dict) and payload.get("auto_reply") is True


def _pending_counts(rows: list[dict], peers: set) -> dict:
    """Candidate unread rows per peer in this batch. Used to WIDEN the thread
    window so the single reply per peer is built over a transcript that covers
    every row later consumed as answered — a burst larger than the default
    window must not be marked read unseen."""
    counts: dict[str, int] = {}
    for m in rows:
        if m.get("type") in relay.RESERVED_TYPES:
            continue
        if not relay.is_relay_message(m, peers):
            continue
        frm = m.get("from_instance", "")
        counts[frm] = counts.get(frm, 0) + 1
    return counts


def _fit(reply: dict) -> dict:
    """Shrink reply['text'] until the compact-JSON payload fits the relay cap.
    UTF-8-safe (slices code points, re-encodes via payload_size)."""
    if relay.payload_size(reply) <= relay.MAX_PAYLOAD_BYTES:
        return reply
    text = reply["text"]
    lo, hi = 0, len(text)
    while lo < hi:                                   # largest prefix that fits
        mid = (lo + hi + 1) // 2
        if relay.payload_size(dict(reply, text=text[:mid])) <= \
                relay.MAX_PAYLOAD_BYTES:
            lo = mid
        else:
            hi = mid - 1
    return dict(reply, text=text[:lo])


def _transcript(rows: list[dict], self_id: str, max_chars: int) -> str:
    """Render a two-party thread as a labelled transcript, reserved-type rows
    excluded (the review channel never enters chat context). R1: clamp to
    max_chars by dropping OLDEST lines, always keeping the newest."""
    lines: list[str] = []
    for m in rows:
        if m.get("type") in relay.RESERVED_TYPES:
            continue
        who = "me" if m.get("from_instance") == self_id else "peer"
        lines.append(f"{who}: {_msg_text(m.get('payload'))}")
    while len(lines) > 1 and len("\n".join(lines)) > max_chars:
        lines.pop(0)
    return "\n".join(lines)


def auto_respond(adapter, liaison_db, self_id, peers, *, limit: int = 10,
                 max_reply_tokens: int = 512, system: str = DEFAULT_SYSTEM,
                 thread_limit: int = 20, max_prompt_chars: int = 8000
                 ) -> list[dict]:
    """Auto-reply to unread peer relay messages. Returns sent packets.
    No-op ([]) when adapter is None, liaison_db/self_id falsy, or peers empty."""
    if adapter is None or not liaison_db or not self_id or not peers:
        return []
    peers = set(peers)
    inbox_rows = mailbox.inbox(liaison_db, self_id, types=None,
                               unread_only=True, limit=limit)  # newest-first
    sent: list[dict] = []
    replied: set[str] = set()
    pending = _pending_counts(inbox_rows, peers)
    for m in inbox_rows:
        typ = m.get("type", "")
        frm = m.get("from_instance", "")
        payload = m.get("payload")
        if typ in relay.RESERVED_TYPES:
            continue                                 # review channel owns it
        if not relay.is_relay_message(m, peers):
            continue                                 # non-peer/oversized: not ours
        if _is_auto_reply(payload):
            mailbox.mark_read(liaison_db, [m["id"]])  # R2: consume, don't answer
            continue
        if frm in replied:
            # newest-first: this peer's newest message was already answered
            # with the full transcript — consume, don't send a duplicate
            mailbox.mark_read(liaison_db, [m["id"]])
            continue
        # window covers the whole pending burst (2x: our replies interleave)
        thread_rows = mailbox.thread(liaison_db, self_id, frm,
                                     limit=thread_limit
                                     + 2 * pending.get(frm, 0))
        transcript = _transcript(thread_rows, self_id, max_prompt_chars)
        prompt = system + "\n\nConversation so far:\n" + transcript
        try:
            text = adapter.generate(prompt, max_tokens=max_reply_tokens).text
        except Exception:                            # fork 4: leave UNREAD, retry
            continue
        reply = _fit({"text": text, "auto_reply": True,
                      "in_reply_to": m["id"]})
        rid = mailbox.send(liaison_db, from_instance=self_id, to_instance=frm,
                           type=typ, payload=reply)   # type echoed
        mailbox.mark_read(liaison_db, [m["id"]])      # R2: per-row, post-send
        sent.append({"to": frm, "in_reply_to": m["id"], "reply_id": rid})
        replied.add(frm)
    return sent
