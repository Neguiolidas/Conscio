# tests/test_relay_respond.py
"""v2.7.0 Phase 2 — pure relay auto-responder. MockAdapter + a real liaison
mailbox on a tmp db; engine-free by contract."""
import ast
import pathlib
import sqlite3

from conscio.agency import relay_respond
from conscio.agency.adapter import AdapterError, MockAdapter
from conscio.liaison import mailbox, relay

PEER = "peer-1111"
OTHER = "stranger-9999"
ME = "me-0000"


def _db(tmp_path):
    return tmp_path / "liaison.db"


def _unread(db):
    con = sqlite3.connect(db)
    n = con.execute(
        "SELECT COUNT(*) FROM messages WHERE read_ts IS NULL").fetchone()[0]
    con.close()
    return n


def test_noop_without_adapter(tmp_path):
    assert relay_respond.auto_respond(None, _db(tmp_path), ME, [PEER]) == []


def test_noop_without_peers(tmp_path):
    a = MockAdapter(script=["hi"])
    assert relay_respond.auto_respond(a, _db(tmp_path), ME, []) == []
    assert a.calls == []


def test_noop_without_self_id(tmp_path):
    a = MockAdapter(script=["hi"])
    assert relay_respond.auto_respond(a, _db(tmp_path), "", [PEER]) == []


def test_one_chat_one_reply(tmp_path):
    db = _db(tmp_path)
    mid = mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                       payload={"text": "ping"})
    a = MockAdapter(script=["pong"])
    sent = relay_respond.auto_respond(a, db, ME, [PEER])
    assert len(sent) == 1
    assert sent[0]["to"] == PEER and sent[0]["in_reply_to"] == mid
    box = mailbox.inbox(db, PEER, unread_only=True)
    assert len(box) == 1
    r = box[0]
    assert r["type"] == "chat"                       # type echoed
    assert r["payload"]["text"] == "pong"
    assert r["payload"]["auto_reply"] is True
    assert r["payload"]["in_reply_to"] == mid
    assert mailbox.inbox(db, ME, unread_only=True) == []   # inbound consumed


def test_auto_reply_inbound_not_answered_but_consumed(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "auto pong", "auto_reply": True})
    a = MockAdapter(script=["should not be used"])
    sent = relay_respond.auto_respond(a, db, ME, [PEER])
    assert sent == []
    assert a.calls == []                             # loop-breaker: no generate
    assert mailbox.inbox(db, ME, unread_only=True) == []   # but consumed


def test_reserved_inbound_untouched(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="review_verdict",
                 payload={"fp": "x", "decision": "approve", "reason": ""})
    a = MockAdapter(script=["nope"])
    assert relay_respond.auto_respond(a, db, ME, [PEER]) == []
    assert a.calls == []
    assert _unread(db) == 1                          # review channel owns it


def test_non_peer_untouched(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=OTHER, to_instance=ME, type="chat",
                 payload={"text": "spam"})
    a = MockAdapter(script=["nope"])
    assert relay_respond.auto_respond(a, db, ME, [PEER]) == []
    assert a.calls == []
    assert _unread(db) == 1                          # relay_inbox/operator owns it


def test_limit_caps_adapter_calls(tmp_path):
    # 15 distinct peers, limit=10 -> at most 10 rows processed / replies sent
    # (same-peer floods collapse to one reply; see one_reply_per_peer test)
    db = _db(tmp_path)
    everyone = [f"peer-{i:04d}" for i in range(15)]
    for p in everyone:
        mailbox.send(db, from_instance=p, to_instance=ME, type="chat",
                     payload={"text": f"hi from {p}"})
    a = MockAdapter(script=["r"] * 100)
    sent = relay_respond.auto_respond(a, db, ME, everyone, limit=10)
    assert len(sent) == 10
    assert len(a.calls) == 10


def test_adapter_error_leaves_unread(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "ping"})

    class _Boom(MockAdapter):
        def generate(self, *a, **k):
            raise AdapterError("backend down")

    assert relay_respond.auto_respond(_Boom(), db, ME, [PEER]) == []
    assert _unread(db) == 1                          # left for retry next cycle


def test_oversized_output_truncated_to_cap(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "ping"})
    huge = "x" * (relay.MAX_PAYLOAD_BYTES * 2)
    a = MockAdapter(script=[huge])
    sent = relay_respond.auto_respond(a, db, ME, [PEER])
    assert len(sent) == 1
    box = mailbox.inbox(db, PEER, unread_only=True)
    assert len(box) == 1
    assert relay.payload_size(box[0]["payload"]) <= relay.MAX_PAYLOAD_BYTES


# ── v2.8.2 "Conversation": multi-turn transcript prompt + R1 budget clamp ─────

def test_multiturn_prompt_includes_history(tmp_path):
    db = _db(tmp_path)
    # history: peer asked, we answered (auto_reply), then peer asks again (unread)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "what is 2+2"}, ts=100.0)
    mailbox.send(db, from_instance=ME, to_instance=PEER, type="chat",
                 payload={"text": "4", "auto_reply": True}, ts=200.0)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "and 3+3"}, ts=300.0)
    captured = {}

    def gen(prompt):
        captured["p"] = prompt
        return "6"

    a = MockAdapter(script=[gen])
    sent = relay_respond.auto_respond(a, db, ME, [PEER])
    assert len(sent) == 1
    assert "what is 2+2" in captured["p"]          # history present
    assert "and 3+3" in captured["p"]              # trigger present
    assert "peer:" in captured["p"] and "me:" in captured["p"]   # labels


def test_single_message_thread_behaves_like_singleshot(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "hello"}, ts=1.0)
    captured = {}
    a = MockAdapter(script=[lambda p: captured.setdefault("p", p) or "hi"])
    sent = relay_respond.auto_respond(a, db, ME, [PEER])
    assert len(sent) == 1
    assert "peer: hello" in captured["p"]
    assert "me:" not in captured["p"]              # no prior turns


def test_budget_clamp_drops_oldest_keeps_newest(tmp_path):
    db = _db(tmp_path)
    for i in range(6):
        mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                     payload={"text": f"OLD{i} " + "x" * 200}, ts=float(i))
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "NEWEST"}, ts=999.0)
    captured = {}
    a = MockAdapter(script=[lambda p: captured.setdefault("p", p) or "ok"])
    relay_respond.auto_respond(a, db, ME, [PEER], max_prompt_chars=300)
    assert "NEWEST" in captured["p"]               # newest always kept
    assert "OLD0" not in captured["p"]             # oldest dropped under budget


def test_reserved_rows_excluded_from_transcript(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="review_request",
                 payload={"text": "SECRET_REVIEW"}, ts=50.0)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "hi there"}, ts=100.0)
    captured = {}
    a = MockAdapter(script=[lambda p: captured.setdefault("p", p) or "yo"])
    relay_respond.auto_respond(a, db, ME, [PEER])
    assert "SECRET_REVIEW" not in captured["p"]    # review channel never in chat
    assert "hi there" in captured["p"]


def test_loop_breaker_consumes_own_auto_reply_unanswered(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "echo", "auto_reply": True}, ts=1.0)
    a = MockAdapter(script=["should-not-be-used"])
    sent = relay_respond.auto_respond(a, db, ME, [PEER])
    assert sent == []                              # not answered
    assert a.calls == []                           # adapter never called
    assert _unread(db) == 0                        # but consumed


def test_relay_respond_imports_no_engine():
    tree = ast.parse(pathlib.Path(relay_respond.__file__).read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            mods.add(n.module)
    assert not any(m == "conscio.engine" or m.startswith("conscio.engine.")
                   for m in mods)


def test_burst_wider_than_thread_window_fully_covered(tmp_path):
    # 30 unread from one peer, limit 50: the ONE reply's transcript must cover
    # every row consumed as answered — including the oldest of the burst
    db = _db(tmp_path)
    for i in range(30):
        mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                     payload={"text": f"burst-{i:02d}"}, ts=float(i))
    captured = {}
    a = MockAdapter(script=[lambda p: captured.setdefault("p", p) or "ok"])
    sent = relay_respond.auto_respond(a, db, ME, [PEER], limit=50)
    assert len(sent) == 1
    assert "burst-00" in captured["p"]         # oldest reached the adapter
    assert _unread(db) == 1                    # all 30 consumed; reply unread


def test_one_reply_per_peer_per_cycle(tmp_path):
    # two unread from the same peer -> ONE reply (the transcript already
    # covers both); the second inbound is consumed as answered
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "q1"})
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "q2"})
    a = MockAdapter(script=["one answer", "must not fire"])
    sent = relay_respond.auto_respond(a, db, ME, [PEER])
    assert len(sent) == 1 and len(a.calls) == 1
    assert _unread(db) == 1                    # both inbound read; reply unread
