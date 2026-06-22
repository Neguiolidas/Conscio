# tests/test_noosphere_catalog.py
from conscio.noosphere import catalog


def _row(iid="A", sha="h1", gt="deploy"):
    return catalog.CatalogRow(
        origin_instance_id=iid, origin_label=f"{iid}-box", goal_fp="fp",
        goal_text=gt, tool_seq='["a"]',
        plan_template='[{"tool":"a","args":{},"rationale":"r"}]',
        published_ts=100.0, content_sha256=sha,
        artifact_json=b'{"x":1}', schema_version=1)


def test_publish_then_read_foreign(tmp_path):
    db = tmp_path / "noosphere.db"
    assert catalog.publish_rows(db, [_row("A", "h1")]) == 1
    rows = catalog.read_foreign(db, exclude_instance_id="B")
    assert len(rows) == 1 and rows[0].origin_instance_id == "A"
    assert rows[0].artifact_json == b'{"x":1}'        # BLOB round-trips


def test_read_foreign_excludes_self(tmp_path):
    db = tmp_path / "noosphere.db"
    catalog.publish_rows(db, [_row("A", "h1")])
    assert catalog.read_foreign(db, exclude_instance_id="A") == []


def test_publish_is_idempotent_and_preserves_published_ts(tmp_path):
    db = tmp_path / "noosphere.db"
    catalog.publish_rows(db, [_row("A", "h1")])
    second = catalog.publish_rows(db, [
        catalog.CatalogRow(**{**_row("A", "h1").__dict__, "published_ts": 999.0})])
    assert second == 0                                # DO NOTHING
    got = catalog.get(db, "A", "h1")
    assert got is not None and got.published_ts == 100.0   # original kept


def test_read_foreign_missing_db_is_empty(tmp_path):
    assert catalog.read_foreign(tmp_path / "nope.db", exclude_instance_id="B") == []


def test_text_coerced_artifact_reads_back_as_bytes(tmp_path):
    # A tampered/edited row whose BLOB got coerced to TEXT (SQLite ||) must not
    # crash the reader; it comes back as bytes so revalidation can reject it.
    import sqlite3
    db = tmp_path / "noosphere.db"
    catalog.publish_rows(db, [_row("A", "h1")])
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE published_skills SET artifact_json = artifact_json || X'20'")
    conn.commit()
    conn.close()
    rows = catalog.read_foreign(db, exclude_instance_id="B")
    assert len(rows) == 1 and isinstance(rows[0].artifact_json, bytes)
    assert rows[0].artifact_json == b'{"x":1} '
