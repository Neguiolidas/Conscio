"""Tests for KnowledgeProjection — read-only KG views."""
from __future__ import annotations
import sqlite3
from pathlib import Path

from conscio.observatory.knowledge_view import KnowledgeProjection


def _make_kg(storage: Path) -> Path:
    """Create a kg.db with 2 entities and 1 triple."""
    db = storage / "kg.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            type TEXT DEFAULT 'unknown', properties TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS triples (
            id TEXT PRIMARY KEY, subject TEXT NOT NULL,
            predicate TEXT NOT NULL, object TEXT NOT NULL,
            valid_from TEXT, valid_to TEXT, confidence REAL DEFAULT 1.0,
            source TEXT, extracted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (subject) REFERENCES entities(id),
            FOREIGN KEY (object) REFERENCES entities(id)
        );
    """)
    conn.execute("INSERT INTO entities VALUES ('python','Python','language','{}','2026-07-25')")
    conn.execute("INSERT INTO entities VALUES ('conscio','Conscio','project','{}','2026-07-25')")
    conn.execute("INSERT INTO triples VALUES ('t1','python','used_by','conscio',NULL,NULL,1.0,'test','2026-07-25')")
    conn.commit()
    conn.close()
    return db


def test_entities_empty_when_no_db(tmp_path):
    proj = KnowledgeProjection(tmp_path)
    assert proj.entities(limit=100) == []


def test_entities_returns_rows(tmp_path):
    _make_kg(tmp_path)
    proj = KnowledgeProjection(tmp_path)
    result = proj.entities(limit=100)
    assert len(result) == 2
    names = {r["name"] for r in result}
    assert names == {"Python", "Conscio"}


def test_entities_respects_limit(tmp_path):
    _make_kg(tmp_path)
    proj = KnowledgeProjection(tmp_path)
    assert len(proj.entities(limit=1)) == 1


def test_relationships_empty_when_no_entity_match(tmp_path):
    _make_kg(tmp_path)
    proj = KnowledgeProjection(tmp_path)
    assert proj.relationships(entity="nonexistent") == []


def test_relationships_returns_triples(tmp_path):
    _make_kg(tmp_path)
    proj = KnowledgeProjection(tmp_path)
    result = proj.relationships(entity="Python")
    assert len(result) == 1
    assert result[0]["predicate"] == "used_by"


def test_relationships_all_when_no_entity(tmp_path):
    _make_kg(tmp_path)
    proj = KnowledgeProjection(tmp_path)
    result = proj.relationships()
    assert len(result) == 1


def test_timeline_empty_when_no_entity(tmp_path):
    _make_kg(tmp_path)
    proj = KnowledgeProjection(tmp_path)
    assert proj.timeline(entity="nonexistent") == []


def test_timeline_returns_events(tmp_path):
    _make_kg(tmp_path)
    proj = KnowledgeProjection(tmp_path)
    result = proj.timeline(entity="Python")
    assert len(result) == 1
