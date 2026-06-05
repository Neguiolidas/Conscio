"""
Migrate — JSON → SQLite migration for Conscio.

One-time migration tool that reads existing JSON files and
imports them into the shared conscio.db database.

Supports:
- goals.json → goals table
- meta_cognition.json → meta_confidence + meta_errors tables
- world_model.json → world_entities + world_relations tables
- evolution_proposals.json → proposals table

After migration, JSON files become read-only backup.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


# ─── Constants ──────────────────────────────────────────────────────────

DEFAULT_STORAGE_PATH = Path.home() / ".hermes" / "consciousness"
DEFAULT_DB_PATH = DEFAULT_STORAGE_PATH / "conscio.db"


# ─── Schema ─────────────────────────────────────────────────────────────

MIGRATION_SCHEMA = """
-- World Model
CREATE TABLE IF NOT EXISTS world_entities (
    name TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT '{}',
    relevance REAL NOT NULL DEFAULT 1.0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS world_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Meta Cognition
CREATE TABLE IF NOT EXISTS meta_confidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'pending',
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta_errors (
    pattern TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Goals
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    drive TEXT NOT NULL,
    priority REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'active',
    meta_score REAL NOT NULL DEFAULT 0.0,
    source TEXT DEFAULT 'internal',
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Evolution Proposals
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    evolution_type TEXT NOT NULL,
    description TEXT NOT NULL,
    rationale TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    risk_level TEXT NOT NULL DEFAULT 'low',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Migration tracking
CREATE TABLE IF NOT EXISTS migration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT NOT NULL,
    records_migrated INTEGER NOT NULL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_drive ON goals(drive);
CREATE INDEX IF NOT EXISTS idx_meta_conf_task ON meta_confidence(task_type);
CREATE INDEX IF NOT EXISTS idx_meta_errors_pattern ON meta_errors(pattern);
CREATE INDEX IF NOT EXISTS idx_world_entities_type ON world_entities(type);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
"""


# ─── Migrator ───────────────────────────────────────────────────────────

class Migrator:
    """
    One-time JSON → SQLite migrator.

    Reads existing JSON files and imports them into conscio.db.
    Idempotent — can be run multiple times without duplicating data.
    """

    def __init__(
        self,
        storage_path: str | Path | None = None,
        db_path: str | Path | None = None,
    ):
        self.storage_path = Path(storage_path) if storage_path else DEFAULT_STORAGE_PATH
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db = sqlite3.connect(str(self.db_path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.row_factory = sqlite3.Row

        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create tables if they don't exist."""
        self.db.executescript(MIGRATION_SCHEMA)
        self.db.commit()

    # ─── Individual Migrations ───────────────────────────────────────

    def migrate_goals(self) -> int:
        """Migrate goals.json → goals table. Returns count of records."""
        json_path = self.storage_path / "goals.json"
        if not json_path.exists():
            return 0

        try:
            data = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError):
            return 0

        if not isinstance(data, list):
            return 0

        count = 0
        for goal in data:
            goal_id = goal.get("id", f"unknown_{count}")
            # Skip if already migrated
            existing = self.db.execute(
                "SELECT id FROM goals WHERE id = ?", (goal_id,)
            ).fetchone()
            if existing:
                continue

            metadata = goal.get("metadata", {})
            self.db.execute(
                """
                INSERT OR IGNORE INTO goals
                    (id, description, drive, priority, status, source, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    goal_id,
                    goal.get("description", ""),
                    goal.get("drive", "unknown"),
                    float(goal.get("priority", 0.5)),
                    goal.get("status", "active"),
                    goal.get("source", "internal"),
                    json.dumps(metadata, default=str),
                    goal.get("created_at", datetime.utcnow().isoformat()),
                ),
            )
            count += 1

        if count > 0:
            self.db.execute(
                "INSERT INTO migration_log (component, records_migrated) VALUES (?, ?)",
                ("goals", count),
            )
            self.db.commit()

        return count

    def migrate_meta_cognition(self) -> int:
        """Migrate meta_cognition.json → meta_confidence + meta_errors. Returns total records."""
        json_path = self.storage_path / "meta_cognition.json"
        if not json_path.exists():
            return 0

        try:
            data = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError):
            return 0

        count = 0

        # Confidence history → meta_confidence
        for entry in data.get("confidence_history", []):
            self.db.execute(
                """
                INSERT INTO meta_confidence (task_type, confidence, outcome, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    entry.get("task_type", "general"),
                    float(entry.get("confidence", 0.5)),
                    entry.get("outcome", "pending"),
                    entry.get("timestamp", datetime.utcnow().isoformat()),
                ),
            )
            count += 1

        # Error patterns → meta_errors
        for entry in data.get("error_patterns", []):
            if isinstance(entry, dict):
                pattern = entry.get("pattern", str(entry))
                count_val = entry.get("count", 1)
                first_seen = entry.get("first_seen", datetime.utcnow().isoformat())
            else:
                pattern = str(entry)
                count_val = 1
                first_seen = datetime.utcnow().isoformat()

            self.db.execute(
                """
                INSERT OR IGNORE INTO meta_errors (pattern, count, first_seen)
                VALUES (?, ?, ?)
                """,
                (pattern, count_val, first_seen),
            )
            count += 1

        if count > 0:
            self.db.execute(
                "INSERT INTO migration_log (component, records_migrated) VALUES (?, ?)",
                ("meta_cognition", count),
            )
            self.db.commit()

        return count

    def migrate_world_model(self) -> int:
        """Migrate world_model.json → world_entities + world_relations. Returns total records."""
        json_path = self.storage_path / "world_model.json"
        if not json_path.exists():
            return 0

        try:
            data = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError):
            return 0

        count = 0

        # Entities
        for entity in data.get("entities", []):
            name = entity.get("name", f"entity_{count}")
            existing = self.db.execute(
                "SELECT name FROM world_entities WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                continue

            self.db.execute(
                """
                INSERT OR IGNORE INTO world_entities
                    (name, type, state, relevance, updated_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    entity.get("type", "unknown"),
                    json.dumps(entity.get("state", {}), default=str),
                    float(entity.get("relevance", 1.0)),
                    entity.get("updated_at", datetime.utcnow().isoformat()),
                    entity.get("created_at", datetime.utcnow().isoformat()),
                ),
            )
            count += 1

        # Relations
        for rel in data.get("relations", []):
            self.db.execute(
                """
                INSERT INTO world_relations (source, target, relation_type, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    rel.get("source", ""),
                    rel.get("target", ""),
                    rel.get("relation_type", "related_to"),
                    datetime.utcnow().isoformat(),
                ),
            )
            count += 1

        if count > 0:
            self.db.execute(
                "INSERT INTO migration_log (component, records_migrated) VALUES (?, ?)",
                ("world_model", count),
            )
            self.db.commit()

        return count

    def migrate_proposals(self) -> int:
        """Migrate evolution_proposals.json → proposals table. Returns count."""
        json_path = self.storage_path / "evolution_proposals.json"
        if not json_path.exists():
            return 0

        try:
            data = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError):
            return 0

        if not isinstance(data, list):
            return 0

        count = 0
        for proposal in data:
            pid = proposal.get("id", f"proposal_{count}")
            existing = self.db.execute(
                "SELECT id FROM proposals WHERE id = ?", (pid,)
            ).fetchone()
            if existing:
                continue

            self.db.execute(
                """
                INSERT OR IGNORE INTO proposals
                    (id, evolution_type, description, rationale, status, risk_level, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    proposal.get("evolution_type", "unknown"),
                    proposal.get("description", ""),
                    proposal.get("rationale", ""),
                    proposal.get("status", "PENDING"),
                    proposal.get("risk_level", "low"),
                    proposal.get("created_at", datetime.utcnow().isoformat()),
                ),
            )
            count += 1

        if count > 0:
            self.db.execute(
                "INSERT INTO migration_log (component, records_migrated) VALUES (?, ?)",
                ("proposals", count),
            )
            self.db.commit()

        return count

    # ─── Full Migration ──────────────────────────────────────────────

    def migrate_all(self) -> dict:
        """
        Run all migrations. Returns dict with counts per component.
        """
        results = {
            "goals": self.migrate_goals(),
            "meta_cognition": self.migrate_meta_cognition(),
            "world_model": self.migrate_world_model(),
            "proposals": self.migrate_proposals(),
        }
        results["total"] = sum(results.values())
        return results

    # ─── Status ──────────────────────────────────────────────────────

    def migration_log(self) -> list[dict]:
        """Return migration log entries."""
        rows = self.db.execute(
            "SELECT component, records_migrated, timestamp FROM migration_log ORDER BY timestamp DESC"
        ).fetchall()
        return [
            {"component": r["component"], "records_migrated": r["records_migrated"], "timestamp": r["timestamp"]}
            for r in rows
        ]

    def table_counts(self) -> dict:
        """Return row counts for all migrated tables."""
        tables = ["goals", "meta_confidence", "meta_errors", "world_entities", "world_relations", "proposals"]
        counts = {}
        for t in tables:
            try:
                c = self.db.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()["c"]
                counts[t] = c
            except sqlite3.OperationalError:
                counts[t] = 0
        return counts

    # ─── Lifecycle ───────────────────────────────────────────────────

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
