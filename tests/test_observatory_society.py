# tests/test_observatory_society.py
"""SocietyProjection: engine-free read-only mode=ro reader over noosphere.db.

WAL regression (Hermet ressalva): the honest scenario is committed-but-
uncheckpointed visibility — a writer (WAL) does INSERT+COMMIT, does NOT
checkpoint, stays OPEN; a mode=ro reader opened afterward sees the committed
row. We do NOT assert uncommitted-WAL visibility (uncommitted rows are
correctly invisible by SQLite isolation). immutable=1 would MISS this row."""
import sqlite3

import pytest

from conscio.observatory.society import SocietyProjection

_SKILLS_DDL = (
    "CREATE TABLE published_skills (origin_instance_id TEXT NOT NULL,"
    " origin_label TEXT NOT NULL, goal_fp TEXT NOT NULL, goal_text TEXT NOT NULL"
    " DEFAULT '', tool_seq TEXT NOT NULL, plan_template TEXT NOT NULL,"
    " published_ts REAL NOT NULL, content_sha256 TEXT NOT NULL,"
    " artifact_json BLOB NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,"
    " PRIMARY KEY (origin_instance_id, content_sha256))")
_RECORDS_DDL = (
    "CREATE TABLE published_records (origin_instance_id TEXT NOT NULL,"
    " origin_label TEXT NOT NULL, published_ts REAL NOT NULL,"
    " content_sha256 TEXT NOT NULL, entry_count INTEGER NOT NULL,"
    " window_first_ts REAL NOT NULL DEFAULT 0, window_last_ts REAL NOT NULL"
    " DEFAULT 0, bundle_json BLOB NOT NULL, schema_version INTEGER NOT NULL"
    " DEFAULT 1, PRIMARY KEY (origin_instance_id, content_sha256))")


def _seed(db):
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SKILLS_DDL + ";" + _RECORDS_DDL)
    conn.execute(
        "INSERT INTO published_skills VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("inst-A", "Alice", "fp1", "ship the thing", "fs_read>fs_write",
         "tmpl-A", 100.0, "sha-A", b"{}", 1))
    conn.execute(
        "INSERT INTO published_records VALUES (?,?,?,?,?,?,?,?,?)",
        ("inst-B", "Bob", 200.0, "sha-B", 5, 10.0, 20.0, b"{}", 1))
    conn.commit()
    conn.close()


def test_skills_shape_metadata_only(tmp_path):
    db = tmp_path / "noosphere.db"
    _seed(db)
    rows = SocietyProjection(db).skills()
    assert len(rows) == 1
    r = rows[0]
    assert r["origin_label"] == "Alice"
    assert r["goal_text"] == "ship the thing"
    assert r["tool_seq"] == "fs_read>fs_write"
    assert "artifact_json" not in r              # BLOB omitted
    assert "plan_template" not in r              # heavy col omitted


def test_records_shape_metadata_only(tmp_path):
    db = tmp_path / "noosphere.db"
    _seed(db)
    rows = SocietyProjection(db).records()
    assert len(rows) == 1
    r = rows[0]
    assert r["origin_label"] == "Bob"
    assert r["entry_count"] == 5
    assert r["window_first_ts"] == 10.0
    assert "bundle_json" not in r               # BLOB omitted


def test_members_census_unions_both_tables(tmp_path):
    db = tmp_path / "noosphere.db"
    _seed(db)
    members = SocietyProjection(db).members()
    by_id = {m["origin_instance_id"]: m for m in members}
    assert set(by_id) == {"inst-A", "inst-B"}
    assert by_id["inst-A"]["skills_count"] == 1
    assert by_id["inst-A"]["records_count"] == 0
    assert by_id["inst-B"]["records_count"] == 1
    assert by_id["inst-B"]["last_published_ts"] == 200.0
    assert members[0]["origin_instance_id"] == "inst-B"   # newest first


def test_missing_db_returns_empty(tmp_path):
    proj = SocietyProjection(tmp_path / "nope.db")
    assert proj.skills() == []
    assert proj.records() == []
    assert proj.members() == []


def test_wal_committed_row_visible_under_concurrent_writer(tmp_path):
    """committed-but-uncheckpointed row, writer still OPEN -> mode=ro sees it."""
    db = tmp_path / "noosphere.db"
    _seed(db)
    writer = sqlite3.connect(str(db))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute(
        "INSERT INTO published_skills VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("inst-C", "Carol", "fp2", "later", "fs_read", "t", 300.0, "sha-C",
         b"{}", 1))
    writer.commit()                              # committed to WAL, NOT checkpointed
    try:
        rows = SocietyProjection(db).skills()    # mode=ro opened while writer OPEN
        assert "Carol" in {r["origin_label"] for r in rows}   # immutable=1 would miss
    finally:
        writer.close()


def test_read_only_connection_rejects_write(tmp_path):
    db = tmp_path / "noosphere.db"
    _seed(db)
    conn = SocietyProjection(db)._ro()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO published_skills VALUES"
                         " ('x','x','x','x','x','x',1.0,'x',?,1)", (b"{}",))
    finally:
        conn.close()
