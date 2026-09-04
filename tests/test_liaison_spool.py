"""Spool store-and-forward: deposit by anyone, ingest only by the owner.

The failure this guards: an agent that is offline when the message is sent
must still receive it — and a crash between INSERT and unlink must not
duplicate the message when the next pass re-reads the same file.
"""
import json

import pytest

from conscio.liaison import directory, mailbox, spool


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSCIO_RELAY_ROOT", str(tmp_path / "relay"))


def _msg(to="me", frm="you", t=1):
    return {"from": frm, "to": to, "type": "relay", "payload": {"t": t}}


def test_deposit_then_ingest(tmp_path):
    spool.deposit("me", _msg())
    db = tmp_path / "me.db"
    assert spool.ingest(db, "me") == 1
    rows = mailbox.inbox(db, "me", unread_only=True)
    assert rows[0]["payload"]["t"] == 1
    assert list(directory.spool_dir("me").glob("*.json")) == []


def test_ingest_twice_is_idempotent_after_crash(tmp_path):
    """Process dies between INSERT and unlink: re-ingest is a no-op."""
    sid = spool.deposit("me", _msg())
    db = tmp_path / "me.db"
    assert spool.ingest(db, "me") == 1
    (directory.spool_dir("me") / f"{sid}.json").write_text(
        json.dumps(_msg()), encoding="utf-8")     # resurrected file
    assert spool.ingest(db, "me") == 0
    assert len(mailbox.inbox(db, "me", unread_only=False)) == 1


def test_malformed_goes_to_quarantine_not_crash(tmp_path):
    d = directory.spool_dir("me")
    d.mkdir(parents=True, exist_ok=True)
    (d / "bad.json").write_text("{{{", encoding="utf-8")
    db = tmp_path / "me.db"
    assert spool.ingest(db, "me") == 0
    assert mailbox.list_quarantine(db) != []
    assert list(d.glob("*.json")) == []


def test_oversized_payload_is_quarantined(tmp_path):
    big = {"from": "you", "to": "me", "type": "relay",
           "payload": {"x": "z" * 200_000}}
    d = directory.spool_dir("me")
    d.mkdir(parents=True, exist_ok=True)
    (d / "big.json").write_text(json.dumps(big), encoding="utf-8")
    db = tmp_path / "me.db"
    assert spool.ingest(db, "me") == 0
    assert mailbox.list_quarantine(db) != []


def test_ingest_is_bounded_per_pass(tmp_path):
    for i in range(5):
        spool.deposit("me", _msg(t=i))
    assert spool.ingest(tmp_path / "me.db", "me", limit=2) == 2


def test_deposit_rejects_traversal_id():
    with pytest.raises(ValueError):
        spool.deposit("../../etc", _msg())


def test_ingest_ignores_message_addressed_elsewhere(tmp_path):
    d = directory.spool_dir("me")
    d.mkdir(parents=True, exist_ok=True)
    (d / "wrong.json").write_text(json.dumps(_msg(to="someone-else")),
                                  encoding="utf-8")
    db = tmp_path / "me.db"
    assert spool.ingest(db, "me") == 0
    assert mailbox.list_quarantine(db) != []


def test_partial_file_is_never_ingested(tmp_path):
    """A writer still streaming its temp file must not be picked up: the
    deposit is only visible under its final name (atomic rename)."""
    d = directory.spool_dir("me")
    d.mkdir(parents=True, exist_ok=True)
    (d / ".half.tmp").write_text('{"from": "you"', encoding="utf-8")
    db = tmp_path / "me.db"
    assert spool.ingest(db, "me") == 0
    assert mailbox.list_quarantine(db) == []
    assert (d / ".half.tmp").exists()


def test_old_db_without_spool_column_is_migrated(tmp_path):
    """A db created before v4.5.4 has no spool_id column: opening it must add
    the column AND the index, never fail indexing a column that isn't there."""
    import sqlite3
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " from_instance TEXT NOT NULL, to_instance TEXT NOT NULL,"
        " type TEXT NOT NULL, payload TEXT NOT NULL, ts REAL NOT NULL,"
        " read_ts REAL);"
        "INSERT INTO messages (from_instance, to_instance, type, payload, ts)"
        " VALUES ('you','me','relay','{}', 1.0);")
    conn.commit()
    conn.close()

    spool.deposit("me", _msg(t=9))
    assert spool.ingest(db, "me") == 1
    rows = mailbox.inbox(db, "me", unread_only=False)
    assert len(rows) == 2                      # the old row survived
