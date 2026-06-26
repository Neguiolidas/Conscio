# tests/test_observatory_liaison.py
import sqlite3

from conscio.observatory.liaison_view import LiaisonProjection


def _seed(db, rows):
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " from_instance TEXT, to_instance TEXT, type TEXT, payload TEXT,"
        " ts REAL, read_ts REAL)")
    conn.executemany(
        "INSERT INTO messages (from_instance,to_instance,type,payload,ts,read_ts)"
        " VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_inbox_missing_db_returns_empty(tmp_path):
    assert LiaisonProjection(tmp_path / "nope.db").inbox("me") == []


def test_inbox_empty_self_id_returns_empty(tmp_path):
    db = tmp_path / "liaison.db"
    _seed(db, [("a", "me", "chat", '{"text":"hi"}', 1.0, None)])
    assert LiaisonProjection(db).inbox("") == []


def test_inbox_filters_to_self_and_parses_payload(tmp_path):
    db = tmp_path / "liaison.db"
    _seed(db, [("a", "me", "chat", '{"text":"hi"}', 1.0, None),
               ("b", "other", "chat", '{"text":"no"}', 2.0, None)])
    inbox = LiaisonProjection(db).inbox("me")
    assert len(inbox) == 1
    assert inbox[0]["payload"] == {"text": "hi"}
    assert inbox[0]["from_instance"] == "a"


def test_inbox_includes_read_and_unread(tmp_path):
    db = tmp_path / "liaison.db"
    _seed(db, [("a", "me", "chat", '{"t":1}', 1.0, 5.0),
               ("a", "me", "chat", '{"t":2}', 2.0, None)])
    assert len(LiaisonProjection(db).inbox("me")) == 2


def test_inbox_never_marks_read(tmp_path):
    db = tmp_path / "liaison.db"
    _seed(db, [("a", "me", "chat", '{"t":1}', 1.0, None)])
    LiaisonProjection(db).inbox("me")
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT read_ts FROM messages").fetchone()[0] is None
    conn.close()


def test_inbox_skips_unparseable_payload(tmp_path):
    db = tmp_path / "liaison.db"
    _seed(db, [("a", "me", "chat", "{not json", 1.0, None),       # R1: bad row
               ("a", "me", "chat", '{"ok":1}', 2.0, None)])
    inbox = LiaisonProjection(db).inbox("me")
    assert len(inbox) == 1 and inbox[0]["payload"] == {"ok": 1}
