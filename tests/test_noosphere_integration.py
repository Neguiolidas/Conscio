# tests/test_noosphere_integration.py
import sqlite3
from conscio.noosphere import publish, importer, quarantine


def _seed(db_path, goal="deploy"):
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        "CREATE TABLE skills (id INTEGER PRIMARY KEY, goal_fp TEXT,"
        " goal_text TEXT, tool_seq TEXT, plan_template TEXT,"
        " successes INT, failures INT);")
    from conscio.agency.fingerprint import goal_fingerprint
    conn.execute(
        "INSERT INTO skills (goal_fp, goal_text, tool_seq, plan_template,"
        " successes, failures) VALUES (?,?,?,?,?,?)",
        (goal_fingerprint(goal), goal, '["a"]',
         '[{"tool":"a","args":{},"rationale":"r"}]', 5, 0))
    conn.commit()
    conn.close()


def test_two_instance_publish_import_cycle(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _seed(a / "conscio.db")
    noo = tmp_path / "noosphere.db"

    assert publish.run(storage=a, noosphere=noo).published == 1
    assert importer.run(storage=b, noosphere=noo).quarantined == 1

    # (1) b's quarantine holds it as 'quarantined'
    qrows = quarantine.list_rows(b / "noosphere_quarantine.db")
    assert len(qrows) == 1 and qrows[0].import_status == "quarantined"

    # (2) b's conscio.db skills table is untouched (b never had one)
    assert not (b / "conscio.db").exists()


def test_tampered_catalog_row_imports_as_rejected(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _seed(a / "conscio.db")
    noo = tmp_path / "noosphere.db"
    publish.run(storage=a, noosphere=noo)

    # hand-tamper the stored BLOB (simulate a poisoned/corrupted catalog)
    conn = sqlite3.connect(str(noo))
    conn.execute("UPDATE published_skills SET artifact_json = artifact_json || X'20'")
    conn.commit()
    conn.close()

    res = importer.run(storage=b, noosphere=noo)
    assert res.rejected == 1 and res.quarantined == 0
    row = quarantine.list_rows(b / "noosphere_quarantine.db")[0]
    assert row.import_status == "rejected" and row.revalidation_result == "tampered"
