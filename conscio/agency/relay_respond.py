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
from ..untrusted import TRANSCRIPT_GUIDANCE, untrusted_envelope

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


def _from_self(m: dict, self_id: str) -> bool:
    return m.get("from_instance") == self_id


def _plain_line(m: dict, text: str, self_id: str) -> str:
    """The labelled line as HEAD rendered it. The budget is measured on these."""
    return ("me: " if _from_self(m, self_id) else "peer: ") + text


def _wrapped_line(m: dict, text: str, self_id: str) -> str:
    """The line that reaches the prompt: our lines as they are, peer lines
    inside their id-bearing envelope (guidance comes once, from the transcript)."""
    if _from_self(m, self_id):
        return f"me: {text}"
    frm = str(m.get("from_instance", ""))
    return "peer: " + untrusted_envelope(
        text, source=f"relay:{frm[:12]}", guidance=False)


def _transcript(rows: list[dict], self_id: str, max_chars: int) -> str:
    """Render a two-party thread as a labelled transcript, reserved-type rows
    excluded (the review channel never enters chat context). The budget is
    measured on the RAW labelled lines, as before the envelope existed: drop
    OLDEST lines while the joined text exceeds max_chars, always keeping the
    newest. Only the survivors are wrapped, so the envelope never removes a
    row the raw rule keeps. The envelope overhead sits on top of max_chars.
    TRANSCRIPT_GUIDANCE is the first line whenever a peer line survives."""
    turns = [(m, _msg_text(m.get("payload"))) for m in rows
             if m.get("type") not in relay.RESERVED_TYPES]
    while len(turns) > 1 and len(_raw_text(turns, self_id)) > max_chars:
        turns.pop(0)
    lines = [_wrapped_line(m, text, self_id) for m, text in turns]
    if all(_from_self(m, self_id) for m, _ in turns):
        return "\n".join(lines)
    return "\n".join([TRANSCRIPT_GUIDANCE, *lines])


def _raw_text(turns: list[tuple[dict, str]], self_id: str) -> str:
    return "\n".join(_plain_line(m, text, self_id) for m, text in turns)


# ── Delta transcript (Ato 1b): incremental context, 70-90% token cut ────────
# The peer's un-answered turns feed the prompt instead of the whole bounded
# thread re-sent each tick. Rows are chronological (oldest-first) from thread().

def _last_outbound_id(thread_rows: list[dict], self_id: str) -> int:
    """The id of the newest row *we* sent in an (oldest-first) thread.

    Everything after it is the peer's newest turn (the delta). If we never
    replied yet (no self row), return 0 → the whole thread is the delta."""
    last = 0
    for m in thread_rows:
        if m.get("from_instance") == self_id:
            last = m["id"]
    return last


def _delta_rows(thread_rows: list[dict], since_id: int) -> list[dict]:
    """Incremental slice: rows newer than since_id (the peer's turns we have
    not yet answered). This is the token cut — only new turns reach the LM."""
    return [m for m in thread_rows if int(m["id"]) > since_id]


def _transcript_delta(thread_rows: list[dict], self_id: str,
                      max_chars: int) -> str:
    """Delta transcript: peer turns since our last reply, clamped to max_chars.
    Excludes already-answered history (vs the full-thread _transcript)."""
    since = _last_outbound_id(thread_rows, self_id)
    return _transcript(_delta_rows(thread_rows, since), self_id, max_chars)


def auto_respond(adapter, liaison_db, self_id, peers, *, limit: int = 10,
                 max_reply_tokens: int = 512, system: str = DEFAULT_SYSTEM,
                 thread_limit: int = 20, max_prompt_chars: int = 8000,
                 delta: bool = False
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
        # Ato 1b: delta=True sends ONLY the peer's un-answered turns (incremental)
        # instead of re-sending the whole bounded thread — 70-90% token cut.
        transcript = _transcript_delta(thread_rows, self_id, max_prompt_chars) \
            if delta else _transcript(thread_rows, self_id, max_prompt_chars)
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
