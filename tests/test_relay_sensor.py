import sqlite3
from pathlib import Path

from conscio.liaison import mailbox
from conscio.perception.relay_sensor import RelaySensor
from conscio.perception.sensor import PerceptionFrame

PEER = "peer-1111"
OTHER = "stranger-9999"
ME = "me-0000"


def _db(tmp_path) -> Path:
    return tmp_path / "liaison.db"


def test_missing_db_quiet(tmp_path):
    s = RelaySensor(_db(tmp_path), ME, [PEER])
    f = s.perceive()
    assert isinstance(f, PerceptionFrame)
    assert f.source == "relay"
    assert f.observations == ["relay: inbox quiet"]
    assert f.signals == {"relay_unread": 0.0, "review_pending": 0.0}


def test_relay_msg_from_peer_counted(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"body": "hi"})
    s = RelaySensor(db, ME, [PEER])
    f = s.perceive()
    assert f.signals["relay_unread"] == 1.0
    assert any("relay: 1 unread from peer-111" in o for o in f.observations)


def test_review_verdict_from_peer_pending(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="review_verdict",
                 payload={"fp": "x", "decision": "approve", "reason": ""})
    s = RelaySensor(db, ME, [PEER])
    f = s.perceive()
    assert f.signals["review_pending"] == 1.0
    assert f.signals["relay_unread"] == 0.0
    assert any("review:" in o for o in f.observations)


def test_review_request_reserved_not_surfaced(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="review_request",
                 payload={"fp": "x"})
    s = RelaySensor(db, ME, [PEER])
    f = s.perceive()
    assert f.signals == {"relay_unread": 0.0, "review_pending": 0.0}
    assert f.observations == ["relay: inbox quiet"]


def test_non_peer_counted_not_detailed(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=OTHER, to_instance=ME, type="chat",
                 payload={"body": "spam"})
    s = RelaySensor(db, ME, [PEER])
    f = s.perceive()
    assert f.signals["relay_unread"] == 0.0
    assert any("non-peers (ignored)" in o for o in f.observations)


def test_perceive_never_marks_read(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"body": "hi"})
    RelaySensor(db, ME, [PEER]).perceive()
    con = sqlite3.connect(db)
    unread = con.execute(
        "SELECT COUNT(*) FROM messages WHERE read_ts IS NULL").fetchone()[0]
    con.close()
    assert unread == 1                      # perception did NOT consume
