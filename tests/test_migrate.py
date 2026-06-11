"""
Tests for Migrate — JSON → SQLite migration.

Covers: each component migration, full migration, idempotency,
edge cases (missing files, invalid JSON, empty data).
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from conscio.migrate import Migrator


@pytest.fixture
def storage(tmp_path):
    """Create a temp storage directory with sample JSON files."""
    s = tmp_path / "consciousness"
    s.mkdir()

    # goals.json
    goals = [
        {
            "id": "curiosity_001",
            "description": "Investigate high CPU load",
            "drive": "curiosity",
            "priority": 2,
            "source": "internal",
            "status": "active",
            "created_at": "2026-06-04T17:04:11",
            "metadata": {"anomaly": "CPU 8.68"},
        },
        {
            "id": "survival_001",
            "description": "Free disk space",
            "drive": "survival",
            "priority": 5,
            "source": "internal",
            "status": "active",
            "created_at": "2026-06-04T18:00:00",
            "metadata": {},
        },
    ]
    (s / "goals.json").write_text(json.dumps(goals, indent=2))

    # meta_cognition.json
    meta = {
        "confidence_history": [
            {"task_type": "general", "confidence": 0.7, "outcome": "pending", "timestamp": "2026-06-04T15:03:19"},
            {"task_type": "trading", "confidence": 0.5, "outcome": "pending", "timestamp": "2026-06-04T15:08:23"},
        ],
        "blind_spots": [],
        "error_patterns": [
            {"pattern": "API timeout", "count": 3, "first_seen": "2026-06-04T10:00:00"},
        ],
        "self_critiques": [],
    }
    (s / "meta_cognition.json").write_text(json.dumps(meta, indent=2))

    # world_model.json
    world = {
        "entities": [
            {"name": "OKX", "type": "exchange", "state": {"status": "connected"}, "relevance": 0.9,
             "updated_at": "2026-06-04T12:00:00", "created_at": "2026-06-01T00:00:00"},
            {"name": "BTC-USDT", "type": "market", "state": {"price": 105000}, "relevance": 0.8,
             "updated_at": "2026-06-04T12:00:00", "created_at": "2026-06-01T00:00:00"},
        ],
        "relations": [
            {"source": "OKX", "target": "BTC-USDT", "relation_type": "lists"},
        ],
    }
    (s / "world_model.json").write_text(json.dumps(world, indent=2))

    # evolution_proposals.json
    proposals = [
        {
            "id": "evo_001",
            "evolution_type": "perception",
            "description": "Add network latency collector",
            "rationale": "Network issues cause trading errors",
            "status": "PENDING",
            "risk_level": "low",
            "created_at": "2026-06-04T16:00:00",
        },
    ]
    (s / "evolution_proposals.json").write_text(json.dumps(proposals, indent=2))

    return s


@pytest.fixture
def migrator(storage, tmp_path):
    """Create a Migrator with temp storage and DB."""
    db_path = tmp_path / "test_migrate.db"
    m = Migrator(storage_path=storage, db_path=db_path)
    yield m
    m.close()


# ─── Schema Tests ───────────────────────────────────────────────────────

class TestSchema:
    def test_all_tables_created(self, migrator):
        tables = migrator.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in tables}
        expected = {"world_entities", "world_relations", "meta_confidence",
                    "meta_errors", "goals", "proposals", "migration_log"}
        assert expected.issubset(names)

    def test_indexes_created(self, migrator):
        indexes = migrator.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        names = {r["name"] for r in indexes}
        expected_subset = {"idx_goals_status", "idx_meta_conf_task", "idx_proposals_status"}
        assert expected_subset.issubset(names)

    def test_wal_mode(self, migrator):
        mode = migrator.db.execute("PRAGMA journal_mode").fetchone()["journal_mode"]
        assert mode == "wal"

    def test_idempotent_schema(self, migrator):
        migrator._ensure_schema()
        migrator._ensure_schema()


# ─── Goals Migration Tests ──────────────────────────────────────────────

class TestMigrateGoals:
    def test_migrate_goals(self, migrator):
        count = migrator.migrate_goals()
        assert count == 2

    def test_goals_in_db(self, migrator):
        migrator.migrate_goals()
        rows = migrator.db.execute("SELECT id, description, drive FROM goals").fetchall()
        assert len(rows) == 2
        ids = {r["id"] for r in rows}
        assert "curiosity_001" in ids
        assert "survival_001" in ids

    def test_goals_idempotent(self, migrator):
        migrator.migrate_goals()
        count2 = migrator.migrate_goals()
        assert count2 == 0  # No new records

    def test_goals_fields(self, migrator):
        migrator.migrate_goals()
        row = migrator.db.execute("SELECT * FROM goals WHERE id = 'curiosity_001'").fetchone()
        assert row["description"] == "Investigate high CPU load"
        assert row["drive"] == "curiosity"
        assert row["status"] == "active"
        assert row["source"] == "internal"

    def test_goals_metadata_stored(self, migrator):
        migrator.migrate_goals()
        row = migrator.db.execute("SELECT metadata FROM goals WHERE id = 'curiosity_001'").fetchone()
        metadata = json.loads(row["metadata"])
        assert metadata["anomaly"] == "CPU 8.68"

    def test_no_goals_file(self, tmp_path):
        """Missing goals.json returns 0."""
        empty_storage = tmp_path / "empty"
        empty_storage.mkdir()
        m = Migrator(storage_path=empty_storage, db_path=tmp_path / "test.db")
        count = m.migrate_goals()
        assert count == 0
        m.close()


# ─── Meta Cognition Migration Tests ─────────────────────────────────────

class TestMigrateMetaCognition:
    def test_migrate_meta(self, migrator):
        count = migrator.migrate_meta_cognition()
        assert count >= 3  # 2 confidence + 1 error pattern

    def test_confidence_in_db(self, migrator):
        migrator.migrate_meta_cognition()
        rows = migrator.db.execute("SELECT task_type, confidence FROM meta_confidence").fetchall()
        assert len(rows) == 2
        types = {r["task_type"] for r in rows}
        assert "general" in types
        assert "trading" in types

    def test_error_patterns_in_db(self, migrator):
        migrator.migrate_meta_cognition()
        rows = migrator.db.execute("SELECT pattern, count FROM meta_errors").fetchall()
        assert len(rows) == 1
        assert rows[0]["pattern"] == "API timeout"
        assert rows[0]["count"] == 3

    def test_empty_meta_file(self, tmp_path):
        """Empty meta_cognition.json."""
        s = tmp_path / "empty"
        s.mkdir()
        (s / "meta_cognition.json").write_text("{}")
        m = Migrator(storage_path=s, db_path=tmp_path / "test.db")
        count = m.migrate_meta_cognition()
        assert count == 0
        m.close()


# ─── World Model Migration Tests ────────────────────────────────────────

class TestMigrateWorldModel:
    def test_migrate_world(self, migrator):
        count = migrator.migrate_world_model()
        assert count >= 3  # 2 entities + 1 relation

    def test_entities_in_db(self, migrator):
        migrator.migrate_world_model()
        rows = migrator.db.execute("SELECT name, type FROM world_entities").fetchall()
        assert len(rows) == 2
        names = {r["name"] for r in rows}
        assert "OKX" in names
        assert "BTC-USDT" in names

    def test_relations_in_db(self, migrator):
        migrator.migrate_world_model()
        rows = migrator.db.execute("SELECT source, target, relation_type FROM world_relations").fetchall()
        assert len(rows) == 1
        assert rows[0]["source"] == "OKX"
        assert rows[0]["relation_type"] == "lists"

    def test_entity_relevance(self, migrator):
        migrator.migrate_world_model()
        row = migrator.db.execute("SELECT relevance FROM world_entities WHERE name = 'OKX'").fetchone()
        assert row["relevance"] == 0.9


# ─── Proposals Migration Tests ──────────────────────────────────────────

class TestMigrateProposals:
    def test_migrate_proposals(self, migrator):
        count = migrator.migrate_proposals()
        assert count == 1

    def test_proposals_in_db(self, migrator):
        migrator.migrate_proposals()
        row = migrator.db.execute("SELECT * FROM proposals WHERE id = 'evo_001'").fetchone()
        assert row["evolution_type"] == "perception"
        assert row["status"] == "PENDING"
        assert row["risk_level"] == "low"

    def test_proposals_idempotent(self, migrator):
        migrator.migrate_proposals()
        count2 = migrator.migrate_proposals()
        assert count2 == 0


# ─── Full Migration Tests ───────────────────────────────────────────────

class TestMigrateAll:
    def test_migrate_all(self, migrator):
        results = migrator.migrate_all()
        assert results["goals"] == 2
        assert results["meta_cognition"] >= 3
        assert results["world_model"] >= 3
        assert results["proposals"] == 1
        assert results["total"] >= 9

    def test_migrate_all_idempotent(self, migrator):
        migrator.migrate_all()
        results2 = migrator.migrate_all()
        # Second run should not add goals or proposals (idempotent)
        assert results2["goals"] == 0
        assert results2["proposals"] == 0

    def test_migration_log(self, migrator):
        migrator.migrate_all()
        log = migrator.migration_log()
        assert len(log) >= 4
        components = {entry["component"] for entry in log}
        assert "goals" in components
        assert "proposals" in components

    def test_table_counts(self, migrator):
        migrator.migrate_all()
        counts = migrator.table_counts()
        assert counts["goals"] == 2
        assert counts["meta_confidence"] == 2
        assert counts["meta_errors"] == 1
        assert counts["world_entities"] == 2
        assert counts["world_relations"] == 1
        assert counts["proposals"] == 1


# ─── Edge Case Tests ────────────────────────────────────────────────────

class TestEdgeCases:
    def test_missing_files(self, tmp_path):
        """All JSON files missing."""
        empty = tmp_path / "empty"
        empty.mkdir()
        m = Migrator(storage_path=empty, db_path=tmp_path / "test.db")
        results = m.migrate_all()
        assert results["total"] == 0
        m.close()

    def test_invalid_json(self, tmp_path):
        """Invalid JSON file."""
        s = tmp_path / "bad"
        s.mkdir()
        (s / "goals.json").write_text("not valid json {{{")
        m = Migrator(storage_path=s, db_path=tmp_path / "test.db")
        count = m.migrate_goals()
        assert count == 0
        m.close()

    def test_empty_json(self, tmp_path):
        """Empty JSON file."""
        s = tmp_path / "empty_json"
        s.mkdir()
        (s / "goals.json").write_text("")
        m = Migrator(storage_path=s, db_path=tmp_path / "test.db")
        count = m.migrate_goals()
        assert count == 0
        m.close()

    def test_shared_db_with_other_modules(self, tmp_path, storage):
        """Migration tables coexist with ContentStore/EventBus tables."""
        from conscio.content_store import ContentStore
        from conscio.event_bus import EventBus

        db_path = tmp_path / "shared.db"
        m = Migrator(storage_path=storage, db_path=db_path)
        m.migrate_all()

        store = ContentStore(db_path=db_path)
        bus = EventBus(db_path=db_path)

        store.index("test", "Some content", "reflection")
        bus.emit("error", "system", {"msg": "test"})

        counts = m.table_counts()
        assert counts["goals"] == 2
        assert store.stats()["source_count"] == 1
        assert bus.stats()["total_events"] == 1

        m.close()
        store.close()
        bus.close()

    def test_context_manager(self, tmp_path, storage):
        """Migrator works as context manager."""
        with Migrator(storage_path=storage, db_path=tmp_path / "ctx.db") as m:
            results = m.migrate_all()
            assert results["total"] >= 9

    def test_close_idempotent(self, migrator):
        migrator.close()
        migrator.close()

    def test_real_consciousness_files(self, tmp_path):
        """Test with actual ~/.hermes/consciousness/ files if they exist."""
        from conscio.migrate import DEFAULT_STORAGE_PATH

        if not DEFAULT_STORAGE_PATH.exists():
            pytest.skip("No real consciousness directory")

        m = Migrator(storage_path=DEFAULT_STORAGE_PATH, db_path=tmp_path / "real.db")
        results = m.migrate_all()
        # Should not error, even if some files are missing
        assert isinstance(results["total"], int)
        assert results["total"] >= 0
        m.close()
