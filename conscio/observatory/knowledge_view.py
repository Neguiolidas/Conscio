"""Read-only projection of the KnowledgeGraph for the Observatory.

Opens kg.db with mode=ro (same pattern as Projection). Uses a local
_entity_id slug matching conscio/kg._entity_id to resolve names to IDs
without importing the read-write KG class.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..guards import clamp_int


def _entity_id(name: str) -> str:
    """Mirror conscio/kg._entity_id — deterministic slug."""
    return name.lower().replace(" ", "_").replace("'", "")


class KnowledgeProjection:
    """Read-only view of the KnowledgeGraph SQLite database.

    Initialized with the instance storage dir; reads storage / "kg.db".
    Never writes — mode=ro.
    """

    def __init__(self, storage: Path) -> None:
        self.storage = Path(storage)
        self._db = self.storage / "kg.db"

    def _ro(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self._db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _select(self, sql: str, params: list) -> list[dict]:
        if not self._db.exists():
            return []
        try:
            conn = self._ro()
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return []
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return []
        finally:
            conn.close()

    def entities(self, *, limit: int = 100) -> list[dict]:
        return self._select(
            "SELECT id, name, type, properties, created_at FROM entities"
            " ORDER BY created_at DESC LIMIT ?",
            [clamp_int(limit, 1, 500)])

    def relationships(self, *, entity: str | None = None,
                      limit: int = 100) -> list[dict]:
        limit = clamp_int(limit, 1, 500)
        if entity is not None:
            eid = _entity_id(entity)
            return self._select(
                "SELECT id, subject, predicate, object, valid_from,"
                " valid_to, confidence, source, extracted_at FROM triples"
                " WHERE subject = ? OR object = ?"
                " ORDER BY COALESCE(valid_from, extracted_at) DESC LIMIT ?",
                [eid, eid, limit])
        return self._select(
            "SELECT id, subject, predicate, object, valid_from,"
            " valid_to, confidence, source, extracted_at FROM triples"
            " ORDER BY COALESCE(valid_from, extracted_at) DESC LIMIT ?",
            [limit])

    def timeline(self, *, entity: str, limit: int = 20) -> list[dict]:
        eid = _entity_id(entity)
        return self._select(
            "SELECT id, subject, predicate, object, valid_from,"
            " valid_to, confidence, source, extracted_at FROM triples"
            " WHERE subject = ? OR object = ?"
            " ORDER BY COALESCE(valid_from, extracted_at) DESC LIMIT ?",
            [eid, eid, clamp_int(limit, 1, 500)])
