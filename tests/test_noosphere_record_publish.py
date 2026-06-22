import json
import sqlite3

from conscio.noosphere import record_catalog, record_publish
from conscio.noosphere.paths import conscio_db_path


def _seed_ledger(storage):
    """Write a minimal real-shaped actions table to <storage>/conscio.db."""
    db = conscio_db_path(storage)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
            goal_fp TEXT NOT NULL, goal_text TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL, args_json TEXT NOT NULL DEFAULT '{}',
            rationale TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL, verdict TEXT NOT NULL DEFAULT '',
            ok INTEGER, output TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '');
    """)
    conn.execute("INSERT INTO actions (ts, goal_fp, goal_text, tool, args_json,"
                 " rationale, tier, status, verdict, ok, output, error) VALUES"
                 " (1.0,'fp1','SECRET intent','write','{\"path\":\"/secret\"}',"
                 " 'because reasons','low','executed','PASS',1,'SECRET out','')")
    conn.execute("INSERT INTO actions (ts, goal_fp, tool, status, ok)"
                 " VALUES (2.0,'fp1','write','failed',0)")
    conn.commit()
    conn.close()


def test_publish_record_creates_one_bundle_and_is_idempotent(tmp_path):
    storage = tmp_path / "A"
    noo = tmp_path / "noosphere.db"
    _seed_ledger(storage)
    res = record_publish.run(storage=storage, noosphere=noo)
    assert res.published == 1 and res.considered == 1 and res.entries == 2
    res2 = record_publish.run(storage=storage, noosphere=noo)
    assert res2.published == 0 and res2.skipped == 1     # identical snapshot


def test_bundle_omits_all_sensitive_columns(tmp_path):
    storage = tmp_path / "A"
    noo = tmp_path / "noosphere.db"
    _seed_ledger(storage)
    record_publish.run(storage=storage, noosphere=noo)
    rows = record_catalog.read_foreign(noo, exclude_instance_id="nobody")
    blob = rows[0].bundle_json.decode("utf-8")
    for leaked in ("SECRET", "args_json", "rationale", "because reasons",
                   "goal_text", "/secret", "output", "error"):
        assert leaked not in blob, f"leaked: {leaked}"
    entry = json.loads(blob)["entries"][0]
    assert set(entry) == {"seq", "ts", "goal_fp", "tool", "tier",
                          "status", "ok", "verdict"}
    assert entry["seq"] == 1                              # seq == actions.id


def test_publish_never_writes_conscio_db(tmp_path):
    storage = tmp_path / "A"
    noo = tmp_path / "noosphere.db"
    _seed_ledger(storage)
    db = conscio_db_path(storage)
    before = db.read_bytes()
    record_publish.run(storage=storage, noosphere=noo)
    assert db.read_bytes() == before                     # opened read-only


def test_missing_db_and_empty_table_are_graceful(tmp_path):
    res = record_publish.run(storage=tmp_path / "absent", noosphere=tmp_path / "n.db")
    assert res == record_publish.PublishRecordResult(0, 0, 0, 0)
