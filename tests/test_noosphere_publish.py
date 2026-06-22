# tests/test_noosphere_publish.py
import sqlite3
import pytest
from conscio.noosphere import publish, catalog, artifact


def _seed_skills(db_path, rows):
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        "CREATE TABLE skills (id INTEGER PRIMARY KEY, goal_fp TEXT,"
        " goal_text TEXT, tool_seq TEXT, plan_template TEXT,"
        " successes INT, failures INT);")
    conn.executemany(
        "INSERT INTO skills (goal_fp, goal_text, tool_seq, plan_template,"
        " successes, failures) VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def _fp(text):
    from conscio.agency.fingerprint import goal_fingerprint
    return goal_fingerprint(text)


def test_publish_only_proven_skills(tmp_path):
    _seed_skills(tmp_path / "conscio.db", [
        (_fp("deploy"), "deploy", '["a"]',
         '[{"tool":"a","args":{},"rationale":"r"}]', 4, 0),
        (_fp("flaky"), "flaky", '["b"]',
         '[{"tool":"b","args":{},"rationale":"r"}]', 1, 3),
    ])
    noo = tmp_path / "noosphere.db"
    res = publish.run(storage=tmp_path, noosphere=noo)
    assert res.published == 1                                  # flaky excluded
    rows = catalog.read_all(noo)
    assert len(rows) == 1 and rows[0].goal_text == "deploy"
    # the published BLOB hashes to the stored content_sha256
    assert artifact.content_hash(rows[0].artifact_json) == rows[0].content_sha256


def test_publish_is_idempotent(tmp_path):
    _seed_skills(tmp_path / "conscio.db", [
        (_fp("deploy"), "deploy", '["a"]',
         '[{"tool":"a","args":{},"rationale":"r"}]', 4, 0)])
    noo = tmp_path / "noosphere.db"
    publish.run(storage=tmp_path, noosphere=noo)
    res2 = publish.run(storage=tmp_path, noosphere=noo)
    assert res2.published == 0 and res2.skipped == 1


def test_missing_conscio_db_publishes_zero(tmp_path):
    res = publish.run(storage=tmp_path, noosphere=tmp_path / "noosphere.db")
    assert res.published == 0 and res.considered == 0


def test_malformed_local_row_is_skipped_not_crashed(tmp_path):
    # proven (rate 1.0) but tool_seq is not valid JSON -> skip, count, no crash
    _seed_skills(tmp_path / "conscio.db", [
        (_fp("deploy"), "deploy", "NOT JSON", "[]", 3, 0),
        (_fp("ok"), "ok", '["a"]',
         '[{"tool":"a","args":{},"rationale":"r"}]', 3, 0),
    ])
    noo = tmp_path / "noosphere.db"
    res = publish.run(storage=tmp_path, noosphere=noo)
    assert res.published == 1 and res.malformed == 1
    assert len(catalog.read_all(noo)) == 1            # catalog not polluted


def test_conscio_db_opened_read_only(tmp_path):
    _seed_skills(tmp_path / "conscio.db", [])
    conn = publish._open_conscio_ro(tmp_path / "conscio.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO skills (goal_fp) VALUES ('x')")
    finally:
        conn.close()
