from conscio.noosphere import record_catalog as rc


def _row(iid="A", sha="h1", **kw):
    base = dict(origin_instance_id=iid, origin_label="labelA", published_ts=1.0,
                content_sha256=sha, entry_count=2, window_first_ts=1.0,
                window_last_ts=2.0, bundle_json=b'{"x":1}', schema_version=1)
    base.update(kw)
    return rc.RecordRow(**base)


def test_publish_is_idempotent_on_pk(tmp_path):
    db = tmp_path / "noosphere.db"
    assert rc.publish_rows(db, [_row()]) == 1
    assert rc.publish_rows(db, [_row()]) == 0          # same (iid, sha) → no-op


def test_read_foreign_excludes_own_and_get_round_trips(tmp_path):
    db = tmp_path / "noosphere.db"
    rc.publish_rows(db, [_row(iid="A", sha="h1"), _row(iid="B", sha="h2")])
    foreign = rc.read_foreign(db, exclude_instance_id="A")
    assert [r.origin_instance_id for r in foreign] == ["B"]
    got = rc.get(db, "A", "h1")
    assert got is not None and got.bundle_json == b'{"x":1}'


def test_as_bytes_handles_text_coerced_blob(tmp_path):
    db = tmp_path / "noosphere.db"
    rc.publish_rows(db, [_row()])
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE published_records SET bundle_json = bundle_json || X'20'")
    conn.commit()
    conn.close()
    got = rc.get(db, "A", "h1")           # TEXT-coerced cell must read as bytes
    assert isinstance(got.bundle_json, bytes)


def test_missing_db_returns_empty(tmp_path):
    db = tmp_path / "absent.db"
    assert rc.read_foreign(db, exclude_instance_id="A") == []
    assert rc.get(db, "A", "h1") is None
