"""KnowledgeGraph — SQLite-backed entities + triples.

Ported from MemPalace 3.6 knowledge_graph.py, simplified for Conscio:
- Removed deps on mempalace.config and mempalace.ids
- IDs generated via deterministic slug: name.lower().replace(" ", "_")
- WAL mode + FK enforcement
- Thread-safe with threading.Lock

Schema:
    entities(id TEXT PK, name, type, properties JSON, created_at)
    triples(id TEXT PK, subject FK, predicate, object FK,
            valid_from, valid_to, confidence, source, extracted_at)
"""
from __future__ import annotations
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entity_id(name: str) -> str:
    """Deterministic slug: 'Grolv.com.br' -> 'grolv.com.br'."""
    return name.lower().replace(" ", "_").replace("'", "")


class KnowledgeGraph:
    """SQLite knowledge graph: entities + triples with WAL persistence."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else Path.home() / ".conscio" / "runtime" / "kg.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _conn_get(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=10, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn_get()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT DEFAULT 'unknown',
                    properties TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS triples (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    confidence REAL DEFAULT 1.0,
                    source TEXT,
                    extracted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (subject) REFERENCES entities(id),
                    FOREIGN KEY (object) REFERENCES entities(id)
                );

                CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject);
                CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object);
                CREATE INDEX IF NOT EXISTS idx_triples_predicate ON triples(predicate);
                """
            )
            conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # ── Write operations ────────────────────────────────────────────

    def add_entity(self, name: str, entity_type: str = "unknown", properties: dict | None = None) -> str:
        """Add or update an entity. Returns the entity id."""
        eid = _entity_id(name)
        props = json.dumps(properties or {}, ensure_ascii=False)
        with self._lock:
            conn = self._conn_get()
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO entities (id, name, type, properties) VALUES (?, ?, ?, ?)",
                    (eid, name, entity_type, props),
                )
        return eid

    def add_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confidence: float = 1.0,
        source: str | None = None,
    ) -> str:
        """Add a relationship triple (subject → predicate → object).

        Both subject and object must already exist as entities (FK enforced).
        Returns the triple id.
        """
        sid = _entity_id(subject)
        oid = _entity_id(obj)
        # Ensure entities exist (auto-create if missing, so caller doesn't need to pre-add)
        # But tests add entities explicitly. Still, auto-create is safer than FK violation.
        with self._lock:
            conn = self._conn_get()
            # Check that both entities exist (FK constraint will fail otherwise)
            for eid in (sid, oid):
                row = conn.execute("SELECT 1 FROM entities WHERE id = ?", (eid,)).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT OR IGNORE INTO entities (id, name, type) VALUES (?, ?, 'unknown')",
                        (eid, eid),
                    )
        tid = f"{sid}->{predicate}->{oid}"
        with self._lock:
            conn = self._conn_get()
            with conn:
                conn.execute(
                    """INSERT OR REPLACE INTO triples
                       (id, subject, predicate, object, valid_from, valid_to, confidence, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (tid, sid, predicate, oid, valid_from, valid_to, confidence, source),
                )
        return tid

    # ── Read operations ──────────────────────────────────────────────

    def query_entity(self, name: str) -> Optional[dict]:
        """Return entity dict by name (or None)."""
        eid = _entity_id(name)
        with self._lock:
            conn = self._conn_get()
            row = conn.execute("SELECT * FROM entities WHERE id = ?", (eid,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def query_relationship(self, name: str) -> list[dict]:
        """Return all triples where entity is subject."""
        eid = _entity_id(name)
        with self._lock:
            conn = self._conn_get()
            rows = conn.execute("SELECT * FROM triples WHERE subject = ?", (eid,)).fetchall()
        return [dict(r) for r in rows]

    def timeline(self, name: str) -> list[dict]:
        """Return triples where entity is subject or object, sorted by extracted_at."""
        eid = _entity_id(name)
        with self._lock:
            conn = self._conn_get()
            rows = conn.execute(
                """SELECT * FROM triples
                   WHERE subject = ? OR object = ?
                   ORDER BY COALESCE(valid_from, extracted_at) DESC""",
                (eid, eid),
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        """Return counts of entities and triples."""
        with self._lock:
            conn = self._conn_get()
            ents = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            trps = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
        return {"entities": ents, "triples": trps}

    # ── Backup (for migration/export) ────────────────────────────────

    def dump(self, target_path: str | Path) -> None:
        """Atomic backup via sqlite3 backup API."""
        import sqlite3 as _sql
        dst = _sql.connect(str(target_path))
        self._conn_get().backup(dst)
        dst.close()
