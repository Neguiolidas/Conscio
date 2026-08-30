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


def test_relay_inbox_groups_by_peer_and_includes_read(tmp_path):
    db = tmp_path / "liaison.db"
    _seed(db, [("p1", "me", "chat", '{"a":1}', 1.0, 5.0),       # lida
               ("p1", "me", "chat", '{"a":2}', 2.0, None),       # não-lida
               ("p2", "me", "chat", '{"b":1}', 3.0, None),
               ("evil", "other", "chat", '{"nope":1}', 4.0, None)])  # outro destino
    groups = LiaisonProjection(db).relay_inbox("me")
    by_peer = {g["from_instance"]: g["messages"] for g in groups}
    assert set(by_peer) == {"p1", "p2"}
    assert len(by_peer["p1"]) == 2              # lida + não-lida, 2 msgs
    assert by_peer["p1"][0]["id"] > by_peer["p1"][1]["id"]   # DESC (nova no topo)
    assert by_peer["p1"][0]["read_ts"] is None
    assert by_peer["p2"][0]["payload"] == {"b": 1}


def test_relay_inbox_excludes_review_types(tmp_path):
    db = tmp_path / "liaison.db"
    _seed(db, [("p1", "me", "chat", '{"t":"x"}', 1.0, None),
               ("p1", "me", "review_request", '{"r":1}', 2.0, None)])
    groups = LiaisonProjection(db).relay_inbox("me")
    only = groups[0]["messages"]
    assert all(m["type"] != "review_request" for m in only)


def test_relay_inbox_missing_db_empty(tmp_path):
    assert LiaisonProjection(tmp_path / "nope.db").relay_inbox("me") == []
