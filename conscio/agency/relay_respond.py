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


def auto_respond(adapter, liaison_db, self_id, peers, *, limit: int = 10,
                 max_reply_tokens: int = 512, system: str = DEFAULT_SYSTEM
                 ) -> list[dict]:
    """Auto-reply to unread peer relay messages. Returns sent packets.
    No-op ([]) when adapter is None, liaison_db/self_id falsy, or peers empty."""
    if adapter is None or not liaison_db or not self_id or not peers:
        return []
    peers = set(peers)
    rows = mailbox.inbox(liaison_db, self_id, types=None,
                         unread_only=True, limit=limit)   # newest-first, capped
    sent: list[dict] = []
    for m in rows:
        typ = m.get("type", "")
        frm = m.get("from_instance", "")
        payload = m.get("payload")
        if typ in relay.RESERVED_TYPES:
            continue                                 # review channel owns it
        if not relay.is_relay_message(m, peers):
            continue                                 # non-peer/oversized: not ours
        if isinstance(payload, dict) and payload.get("auto_reply") is True:
            mailbox.mark_read(liaison_db, [m["id"]])  # R2: consume, don't answer
            continue
        prompt = system + "\n\nPeer message:\n" + _msg_text(payload)
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
    return sent
