# tests/test_noosphere_quarantine.py
from conscio.noosphere import quarantine


def _qrow(sha="h1", status="quarantined", result="ok"):
    return quarantine.QuarantineRow(
        content_sha256=sha, origin_instance_id="A", origin_label="A-box",
        published_ts=1.0, importer_instance_id="B", imported_ts=2.0,
        goal_fp="fp", goal_text="deploy", tool_seq='["a"]',
        plan_template='[{"tool":"a","args":{},"rationale":"r"}]',
        artifact_json=b'{"x":1}', import_status=status,
        revalidation_result=result, revalidation_error="", schema_version=1)


def test_insert_then_list(tmp_path):
    db = tmp_path / "q.db"
    assert quarantine.insert(db, _qrow()) is True
    rows = quarantine.list_rows(db)
    assert len(rows) == 1 and rows[0].import_status == "quarantined"
    assert rows[0].id is not None and rows[0].artifact_json == b'{"x":1}'


def test_duplicate_insert_returns_false(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _qrow("h1"))
    assert quarantine.insert(db, _qrow("h1")) is False     # UNIQUE
    assert len(quarantine.list_rows(db)) == 1


def test_rejected_rows_are_recorded(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _qrow("h2", status="rejected", result="tampered"))
    rows = quarantine.list_rows(db)
    assert rows[0].import_status == "rejected"
    assert rows[0].revalidation_result == "tampered"


def test_get_by_rowid(tmp_path):
    db = tmp_path / "q.db"
    quarantine.insert(db, _qrow("h1"))
    rid = quarantine.list_rows(db)[0].id
    assert quarantine.get(db, rid).content_sha256 == "h1"
    assert quarantine.get(db, 9999) is None
