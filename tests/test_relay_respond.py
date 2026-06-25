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
    db = _db(tmp_path)
    for i in range(15):
        mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                     payload={"text": f"m{i}"})
    a = MockAdapter(script=["r"] * 100)
    sent = relay_respond.auto_respond(a, db, ME, [PEER], limit=10)
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
