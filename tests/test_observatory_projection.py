# tests/test_observatory_projection.py
import json
import sqlite3

import pytest

from conscio.observatory.projection import Projection


def _db(tmp_path):
    """Create a conscio.db with the three tables the projection reads."""
    conn = sqlite3.connect(str(tmp_path / "conscio.db"))
    conn.executescript("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
            category TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
            priority INTEGER NOT NULL DEFAULT 5, data_hash TEXT NOT NULL,
            project_dir TEXT DEFAULT '', attribution_confidence REAL DEFAULT 0.0,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            is_duplicate INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
            goal_fp TEXT NOT NULL, goal_text TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL, args_json TEXT NOT NULL,
            rationale TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL, verdict TEXT NOT NULL DEFAULT '',
            verdict_reasons TEXT NOT NULL DEFAULT '', ok INTEGER,
            output TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0, adapter TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '', approval_policy TEXT NOT NULL DEFAULT '');
        CREATE TABLE skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts REAL NOT NULL,
            goal_fp TEXT NOT NULL, goal_text TEXT NOT NULL DEFAULT '',
            tool_seq TEXT NOT NULL, plan_template TEXT NOT NULL,
            successes INTEGER NOT NULL DEFAULT 1, failures INTEGER NOT NULL DEFAULT 0,
            uses INTEGER NOT NULL DEFAULT 0, last_used_ts REAL NOT NULL DEFAULT 0);
    """)
    conn.execute("INSERT INTO events (type, category, data, data_hash, timestamp)"
                 " VALUES ('reflection','consciousness','{\"k\":1}','h1','2024-06-01 12:00:00')")
    conn.execute("INSERT INTO actions (ts, goal_fp, tool, args_json, status)"
                 " VALUES (1.0,'fp','fs_read','{}','executed')")
    conn.execute("INSERT INTO skills (created_ts, goal_fp, tool_seq, plan_template)"
                 " VALUES (1.0,'fp','[\"fs_read\"]','[]')")
    conn.commit()
    conn.close()


def test_events_actions_skills_read_seeded_rows(tmp_path):
    _db(tmp_path)
    p = Projection(tmp_path)
    evs = p.events()
    assert len(evs) == 1 and evs[0]["type"] == "reflection"
    assert evs[0]["data"] == {"k": 1}                 # TEXT JSON decoded
    assert len(p.actions()) == 1 and p.actions()[0]["tool"] == "fs_read"
    assert len(p.skills()) == 1 and p.skills()[0]["goal_fp"] == "fp"


def test_goals_reads_a_valid_json_list(tmp_path):
    # Regression: goals.json is a LIST; safe_read_json (dict-only) must NOT be
    # used or a valid file comes back empty.
    (tmp_path / "goals.json").write_text(
        json.dumps([{"description": "do x", "drive": "curiosity"}]))
    p = Projection(tmp_path)
    goals = p.goals()
    assert len(goals) == 1 and goals[0]["description"] == "do x"


def test_state_reads_a_valid_dict(tmp_path):
    (tmp_path / "state_summary.json").write_text(json.dumps({"awake": True}))
    assert Projection(tmp_path).state() == {"awake": True}


def test_missing_and_corrupt_degrade_to_empty(tmp_path):
    p = Projection(tmp_path)                          # nothing on disk
    assert p.events() == [] and p.actions() == [] and p.skills() == []
    assert p.goals() == [] and p.state() == {}
    (tmp_path / "goals.json").write_text("{ not json")
    (tmp_path / "state_summary.json").write_text("[1,2,3]")  # list, not object
    assert p.goals() == [] and p.state() == {}


def test_connection_is_read_only(tmp_path):
    _db(tmp_path)
    p = Projection(tmp_path)
    conn = p._ro()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO events (type,category,data_hash)"
                         " VALUES ('x','system','h')")
    finally:
        conn.close()


def test_since_filters_lexically(tmp_path):
    _db(tmp_path)                                     # event ts "2024-06-01 12:00:00"
    p = Projection(tmp_path)
    assert len(p.events(since="2024-01-01 00:00:00")) == 1
    assert len(p.events(since="2024-12-01 00:00:00")) == 0
