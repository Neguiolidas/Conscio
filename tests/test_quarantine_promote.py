# tests/test_quarantine_promote.py
import sqlite3

from conscio.noosphere import quarantine
from conscio.noosphere.quarantine import QuarantineRow


def _row():
    return QuarantineRow(
        content_sha256="h", origin_instance_id="o", origin_label="A",
        published_ts=1.0, importer_instance_id="i", imported_ts=2.0,
        goal_fp="fp", goal_text="g", tool_seq="[]", plan_template="[]",
        artifact_json=b"{}", import_status="quarantined",
        revalidation_result="ok", revalidation_error="", schema_version=1)


def test_promote_columns_default_zero(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _row())
    got = quarantine.get(db, 1)
    assert got.promoted_ts == 0.0 and got.promoted_skill_id == 0


def test_mark_promoted_stamps(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _row())
    assert quarantine.mark_promoted(db, 1, ts=123.0, skill_id=7) is True
    got = quarantine.get(db, 1)
    assert got.promoted_ts == 123.0 and got.promoted_skill_id == 7


def test_mark_promoted_missing_row(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _row())
    assert quarantine.mark_promoted(db, 99, ts=1.0, skill_id=1) is False


def test_migration_adds_promote_columns_to_old_db(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE quarantine (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " content_sha256 TEXT NOT NULL, origin_instance_id TEXT NOT NULL,"
        " origin_label TEXT NOT NULL, published_ts REAL NOT NULL,"
        " importer_instance_id TEXT NOT NULL, imported_ts REAL NOT NULL,"
        " goal_fp TEXT NOT NULL, goal_text TEXT NOT NULL DEFAULT '',"
        " tool_seq TEXT NOT NULL, plan_template TEXT NOT NULL,"
        " artifact_json BLOB NOT NULL, import_status TEXT NOT NULL,"
        " revalidation_result TEXT NOT NULL,"
        " revalidation_error TEXT NOT NULL DEFAULT '',"
        " schema_version INTEGER NOT NULL DEFAULT 1,"
        " UNIQUE(origin_instance_id, content_sha256));")
    conn.execute(
        "INSERT INTO quarantine (content_sha256, origin_instance_id,"
        " origin_label, published_ts, importer_instance_id, imported_ts,"
        " goal_fp, goal_text, tool_seq, plan_template, artifact_json,"
        " import_status, revalidation_result) VALUES"
        " ('h','o','A',1.0,'i',2.0,'fp','g','[]','[]',X'7b7d',"
        " 'quarantined','ok');")
    conn.commit()
    conn.close()
    got = quarantine.get(db, 1)                 # opening must migrate
    assert got is not None and got.promoted_ts == 0.0
    cols = {r[1] for r in sqlite3.connect(str(db)).execute(
        "PRAGMA table_info(quarantine)")}
    assert {"promoted_ts", "promoted_skill_id"} <= cols
