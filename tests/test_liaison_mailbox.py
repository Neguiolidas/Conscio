# tests/test_liaison_mailbox.py
from conscio.liaison import mailbox


def test_send_then_inbox_roundtrip(tmp_path):
    db = tmp_path / "liaison.db"
    mailbox.send(db, from_instance="A", to_instance="B", type="review_request",
                 payload={"fp": "x", "tool": "echo"})
    rows = mailbox.inbox(db, "B")
    assert len(rows) == 1
    assert rows[0]["from_instance"] == "A"
    assert rows[0]["type"] == "review_request"
    assert rows[0]["payload"] == {"fp": "x", "tool": "echo"}   # parsed, not str
    assert rows[0]["read_ts"] is None


def test_inbox_is_directed_only(tmp_path):
    db = tmp_path / "liaison.db"
    mailbox.send(db, from_instance="A", to_instance="B", type="review_request",
                 payload={"fp": "1"})
    mailbox.send(db, from_instance="A", to_instance="C", type="review_request",
                 payload={"fp": "2"})
    assert [r["payload"]["fp"] for r in mailbox.inbox(db, "B")] == ["1"]
    assert [r["payload"]["fp"] for r in mailbox.inbox(db, "C")] == ["2"]


def test_inbox_filters_by_type_and_unread(tmp_path):
    db = tmp_path / "liaison.db"
    mailbox.send(db, from_instance="A", to_instance="B", type="review_request",
                 payload={"fp": "r"})
    mailbox.send(db, from_instance="A", to_instance="B", type="review_verdict",
                 payload={"fp": "v"})
    only_v = mailbox.inbox(db, "B", types=["review_verdict"])
    assert [r["type"] for r in only_v] == ["review_verdict"]


def test_mark_read_flips_only_named_unread_rows(tmp_path):
    db = tmp_path / "liaison.db"
    mid = mailbox.send(db, from_instance="A", to_instance="B",
                       type="review_request", payload={"fp": "1"})
    assert mailbox.mark_read(db, [mid]) == 1
    assert mailbox.inbox(db, "B", unread_only=True) == []        # now hidden
    assert len(mailbox.inbox(db, "B", unread_only=False)) == 1   # still present
    assert mailbox.mark_read(db, [mid]) == 0                     # idempotent


def test_per_row_read_ts_independence(tmp_path):
    db = tmp_path / "liaison.db"
    m1 = mailbox.send(db, from_instance="A", to_instance="B",
                      type="review_request", payload={"fp": "1"})
    mailbox.send(db, from_instance="A", to_instance="B",
                 type="review_request", payload={"fp": "2"})
    mailbox.mark_read(db, [m1])
    left = mailbox.inbox(db, "B", unread_only=True)
    assert [r["payload"]["fp"] for r in left] == ["2"]


def test_missing_db_inbox_returns_empty(tmp_path):
    assert mailbox.inbox(tmp_path / "nope.db", "B") == []
    assert mailbox.mark_read(tmp_path / "nope.db", [1]) == 0


def test_corrupt_db_inbox_returns_empty(tmp_path):
    db = tmp_path / "liaison.db"
    db.write_bytes(b"this is not a sqlite database")
    assert mailbox.inbox(db, "B") == []


def test_first_send_creates_db(tmp_path):
    db = tmp_path / "sub" / "liaison.db"
    assert not db.exists()
    mailbox.send(db, from_instance="A", to_instance="B", type="review_request",
                 payload={})
    assert db.exists()


def test_purge_read_deletes_old_read(tmp_path):
    import time
    db = tmp_path / "liaison.db"
    old = time.time() - 8 * 86400
    mid = mailbox.send(db, from_instance="A", to_instance="B",
                       type="note", payload={}, ts=old)
    mailbox.mark_read(db, [mid], read_ts=old)
    assert mailbox.purge_read(db, 7.0) == 1
    assert mailbox.inbox(db, "B", unread_only=False) == []


def test_purge_read_keeps_recent_read(tmp_path):
    db = tmp_path / "liaison.db"
    mid = mailbox.send(db, from_instance="A", to_instance="B",
                       type="note", payload={})
    mailbox.mark_read(db, [mid])
    assert mailbox.purge_read(db, 7.0) == 0
    assert len(mailbox.inbox(db, "B", unread_only=False)) == 1


def test_purge_read_keeps_unread_old(tmp_path):
    import time
    db = tmp_path / "liaison.db"
    old = time.time() - 30 * 86400
    mailbox.send(db, from_instance="A", to_instance="B",
                 type="note", payload={}, ts=old)
    assert mailbox.purge_read(db, 7.0) == 0            # unread never deleted
    assert len(mailbox.inbox(db, "B", unread_only=True)) == 1


def test_purge_read_missing_db(tmp_path):
    assert mailbox.purge_read(tmp_path / "nope.db", 7.0) == 0
