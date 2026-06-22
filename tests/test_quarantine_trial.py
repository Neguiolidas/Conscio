# tests/test_quarantine_trial.py
import sqlite3
from conscio.noosphere import quarantine
from conscio.noosphere.quarantine import QuarantineRow


def _row(**kw) -> QuarantineRow:
    base = dict(
        content_sha256="abc", origin_instance_id="o1", origin_label="A",
        published_ts=1.0, importer_instance_id="i1", imported_ts=2.0,
        goal_fp="fp", goal_text="deploy", tool_seq='["fs_write"]',
        plan_template='[{"tool":"fs_write","args":{},"rationale":"r"}]',
        artifact_json=b"{}", import_status="quarantined",
        revalidation_result="ok", revalidation_error="", schema_version=1)
    base.update(kw)
    return QuarantineRow(**base)


def test_new_db_has_trial_columns_and_defaults(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _row())
    got = quarantine.get(db, 1)
    assert got.trial_successes == 0
    assert got.trial_failures == 0
    assert got.last_trial_ts == 0
    assert got.last_trial_result == ""
    assert got.last_trial_error == ""


def test_migration_adds_columns_to_pre_v222_db(tmp_path):
    # Build an OLD-schema quarantine table (no trial_* columns), then open
    # via _connect (which must migrate) and confirm the columns appear.
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
    # opening through _connect must migrate; get() must succeed with defaults
    got = quarantine.get(db, 1)
    assert got is not None
    assert got.trial_successes == 0 and got.last_trial_result == ""
    cols = {r[1] for r in sqlite3.connect(str(db)).execute(
        "PRAGMA table_info(quarantine)")}
    assert {"trial_successes", "trial_failures", "last_trial_ts",
            "last_trial_result", "last_trial_error"} <= cols


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _row())
    quarantine.get(db, 1)              # second _connect must not error
    quarantine.get(db, 1)


def test_record_trial_pass_bumps_successes(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _row())
    assert quarantine.record_trial(
        db, 1, passed=True, result="passed", error="", ts=9.0) is True
    got = quarantine.get(db, 1)
    assert got.trial_successes == 1 and got.trial_failures == 0
    assert got.last_trial_result == "passed" and got.last_trial_ts == 9.0


def test_record_trial_fail_bumps_failures(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _row())
    quarantine.record_trial(db, 1, passed=False,
                            result="exec_fail:fs_read", error="boom", ts=3.0)
    got = quarantine.get(db, 1)
    assert got.trial_failures == 1 and got.trial_successes == 0
    assert got.last_trial_result == "exec_fail:fs_read"
    assert got.last_trial_error == "boom"


def test_note_trial_sets_fields_without_bumping_counts(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _row())
    assert quarantine.note_trial(
        db, 1, result="tampered", error="mismatch", ts=5.0) is True
    got = quarantine.get(db, 1)
    assert got.trial_successes == 0 and got.trial_failures == 0
    assert got.last_trial_result == "tampered"
    assert got.last_trial_error == "mismatch" and got.last_trial_ts == 5.0


def test_record_trial_unknown_row_returns_false(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _row())
    assert quarantine.record_trial(
        db, 999, passed=True, result="passed", error="", ts=1.0) is False
