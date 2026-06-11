"""
EventBus — Session event tracking with dedup.

Records everything that happens (perceptions, errors, decisions, trades,
reflections) with timestamp, type, priority, and dedup by content hash.

Shares the same SQLite database as ContentStore (conscio.db).

Inspired by context-mode/src/session/db.ts — reimplemented 100% in Python.
No MCP, no Node.js, no external deps.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# ─── Data Classes ───────────────────────────────────────────────────────

@dataclass
class Event:
    """A single event in the EventBus."""
    id: int
    type: str
    category: str
    data: dict
    priority: int
    data_hash: str
    project_dir: str
    attribution_confidence: float
    timestamp: str
    is_duplicate: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "category": self.category,
            "data": self.data,
            "priority": self.priority,
            "data_hash": self.data_hash,
            "project_dir": self.project_dir,
            "attribution_confidence": self.attribution_confidence,
            "timestamp": self.timestamp,
            "is_duplicate": self.is_duplicate,
        }


# ─── Constants ──────────────────────────────────────────────────────────

DEFAULT_DB_PATH = Path.home() / ".hermes" / "consciousness" / "conscio.db"

VALID_TYPES = {
 "tool_call", "reflection", "trade", "error", "anomaly",
 "decision", "perception", "goal_created", "goal_expired",
 "evolution_proposed", "system", "consciousness", "session",
 "coherence:dissonance",  # v0.6: passive low-coherence signal (advisory)
}

VALID_CATEGORIES = {"system", "trading", "consciousness", "external", "session"}

# Priority scale: 0 = critical (life-threatening/error), 10 = trivial
PRIORITY_CRITICAL = 0
PRIORITY_HIGH = 2
PRIORITY_NORMAL = 5
PRIORITY_LOW = 8
PRIORITY_TRIVIAL = 10

# Dedup window: don't emit the same event within N seconds
DEDUP_WINDOW_SECONDS = 60


# ─── EventBus ───────────────────────────────────────────────────────────

class EventBus:
    """
    Session event tracking with dedup by content hash.

    Events are recorded with timestamps, types, priorities, and
    JSON payloads. Duplicate events (same type + data hash) within
    the dedup window are marked but not re-inserted.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db = sqlite3.connect(str(self.db_path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.row_factory = sqlite3.Row

        self._init_schema()

    # ─── Schema ──────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        """Initialize events table and indexes."""
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                category TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '{}',
                priority INTEGER NOT NULL DEFAULT 5,
                data_hash TEXT NOT NULL,
                project_dir TEXT DEFAULT '',
                attribution_confidence REAL DEFAULT 0.0,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                is_duplicate INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
            CREATE INDEX IF NOT EXISTS idx_events_priority ON events(priority);
            CREATE INDEX IF NOT EXISTS idx_events_hash ON events(data_hash);
        """)
        self.db.commit()

    # ─── Emit ────────────────────────────────────────────────────────

    def emit(
        self,
        type: str,
        category: str,
        data: dict,
        priority: int = PRIORITY_NORMAL,
        project_dir: str = "",
        attribution_confidence: float = 0.0,
    ) -> int:
        """
        Emit an event. Returns event ID.

        Dedup: if an event with the same type + data_hash exists within
        the dedup window (60s by default), it's marked as duplicate
        but NOT re-inserted (returns the existing event's ID).

        Args:
            type: Event type (one of VALID_TYPES)
            category: Event category (one of VALID_CATEGORIES)
            data: JSON-serializable payload
            priority: 0=critical, 10=trivial
            project_dir: Project attribution path
            attribution_confidence: 0.0-1.0 confidence

        Returns:
            Event ID (new or existing if duplicate)
        """
        if type not in VALID_TYPES:
            raise ValueError(f"Invalid event type '{type}'. Must be one of: {VALID_TYPES}")
        if category not in VALID_CATEGORIES:
            raise ValueError(f"Invalid event category '{category}'. Must be one of: {VALID_CATEGORIES}")

        data_json = json.dumps(data, sort_keys=True, default=str)
        data_hash = hashlib.sha256(data_json.encode()).hexdigest()
        timestamp = datetime.utcnow().isoformat()

        # Check for recent duplicate (same type + hash within dedup window)
        cutoff = (datetime.utcnow() - timedelta(seconds=DEDUP_WINDOW_SECONDS)).isoformat()
        existing = self.db.execute(
            """
            SELECT id FROM events
            WHERE type = ? AND data_hash = ? AND timestamp >= ?
            LIMIT 1
            """,
            (type, data_hash, cutoff),
        ).fetchone()

        if existing:
            # Mark as duplicate but don't re-insert
            return existing["id"]

        cursor = self.db.execute(
            """
            INSERT INTO events (type, category, data, priority, data_hash, project_dir, attribution_confidence, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (type, category, data_json, priority, data_hash, project_dir, attribution_confidence, timestamp),
        )
        self.db.commit()

        return cursor.lastrowid

    def emit_batch(self, events: list[dict]) -> list[int]:
        """
        Emit multiple events in a single transaction.

        Each dict must have: type, category, data.
        Optional: priority, project_dir, attribution_confidence.

        Returns list of event IDs.
        """
        ids = []
        for evt in events:
            eid = self.emit(
                type=evt["type"],
                category=evt["category"],
                data=evt.get("data", {}),
                priority=evt.get("priority", PRIORITY_NORMAL),
                project_dir=evt.get("project_dir", ""),
                attribution_confidence=evt.get("attribution_confidence", 0.0),
            )
            ids.append(eid)
        return ids

    # ─── Query ───────────────────────────────────────────────────────

    def query(
        self,
        type: str | None = None,
        category: str | None = None,
        since: str | None = None,
        until: str | None = None,
        priority_max: int | None = None,
        project_dir: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_duplicates: bool = False,
    ) -> list[Event]:
        """
        Query events with flexible filters.

        Args:
            type: Filter by event type
            category: Filter by category
            since: ISO timestamp — events after this time
            until: ISO timestamp — events before this time
            priority_max: Max priority value (0=most critical)
            project_dir: Filter by project directory
            limit: Max results
            offset: Skip first N results
            include_duplicates: Include events marked as duplicates

        Returns:
            List of Event objects, newest first
        """
        conditions = []
        params = []

        if type:
            conditions.append("type = ?")
            params.append(type)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp <= ?")
            params.append(until)
        if priority_max is not None:
            conditions.append("priority <= ?")
            params.append(priority_max)
        if project_dir:
            conditions.append("project_dir = ?")
            params.append(project_dir)
        if not include_duplicates:
            conditions.append("is_duplicate = 0")

        where = " AND ".join(conditions) if conditions else "1=1"

        rows = self.db.execute(
            f"""
            SELECT id, type, category, data, priority, data_hash,
                   project_dir, attribution_confidence, timestamp, is_duplicate
            FROM events
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

        return [self._row_to_event(r) for r in rows]

    def recent_errors(self, limit: int = 10) -> list[Event]:
        """Shortcut: recent error events — feeds MetaCognition."""
        return self.query(type="error", priority_max=PRIORITY_HIGH, limit=limit)

    def recent_anomalies(self, limit: int = 10) -> list[Event]:
        """Shortcut: recent anomaly events."""
        return self.query(type="anomaly", limit=limit)

    def get(self, event_id: int) -> Optional[Event]:
        """Get a single event by ID."""
        row = self.db.execute(
            """
            SELECT id, type, category, data, priority, data_hash,
                   project_dir, attribution_confidence, timestamp, is_duplicate
            FROM events WHERE id = ?
            """,
            (event_id,),
        ).fetchone()

        return self._row_to_event(row) if row else None

    # ─── Summary ─────────────────────────────────────────────────────

    def summary(self, hours: int = 24) -> dict:
        """
        Activity summary for the last N hours.

        Returns counts by type, category, priority distribution,
        and error/anomaly highlights.
        """
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

        # Total count
        total = self.db.execute(
            "SELECT COUNT(*) as c FROM events WHERE timestamp >= ?",
            (cutoff,),
        ).fetchone()["c"]

        # By type
        by_type_rows = self.db.execute(
            "SELECT type, COUNT(*) as c FROM events WHERE timestamp >= ? GROUP BY type",
            (cutoff,),
        ).fetchall()
        by_type = {r["type"]: r["c"] for r in by_type_rows}

        # By category
        by_cat_rows = self.db.execute(
            "SELECT category, COUNT(*) as c FROM events WHERE timestamp >= ? GROUP BY category",
            (cutoff,),
        ).fetchall()
        by_category = {r["category"]: r["c"] for r in by_cat_rows}

        # By priority bucket
        priority_buckets = {"critical": 0, "high": 0, "normal": 0, "low": 0, "trivial": 0}
        for r in self.db.execute(
            "SELECT priority, COUNT(*) as c FROM events WHERE timestamp >= ? GROUP BY priority",
            (cutoff,),
        ).fetchall():
            p = r["priority"]
            if p <= 1:
                priority_buckets["critical"] += r["c"]
            elif p <= 3:
                priority_buckets["high"] += r["c"]
            elif p <= 6:
                priority_buckets["normal"] += r["c"]
            elif p <= 8:
                priority_buckets["low"] += r["c"]
            else:
                priority_buckets["trivial"] += r["c"]

        # Duplicate count
        dup_count = self.db.execute(
            "SELECT COUNT(*) as c FROM events WHERE timestamp >= ? AND is_duplicate = 1",
            (cutoff,),
        ).fetchone()["c"]

        # Latest errors (up to 3)
        latest_errors = self.query(type="error", limit=3, include_duplicates=False)
        error_highlights = [e.data for e in latest_errors]

        return {
            "hours": hours,
            "total_events": total,
            "duplicates_suppressed": dup_count,
            "by_type": by_type,
            "by_category": by_category,
            "priority_distribution": priority_buckets,
            "error_highlights": error_highlights,
        }

    # ─── Maintenance ─────────────────────────────────────────────────

    def compact(self, before_days: int = 30) -> int:
        """
        Compact old events.

        Strategy:
        1. Remove trivial events (priority >= 8) older than before_days
        2. Remove duplicate events older than before_days
        3. Keep all critical/high priority events

        Returns number of events removed.
        """
        cutoff = (datetime.utcnow() - timedelta(days=before_days)).isoformat()

        # Count before
        before_count = self.db.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]

        # Remove trivial old events
        self.db.execute(
            "DELETE FROM events WHERE timestamp < ? AND priority >= ?",
            (cutoff, PRIORITY_LOW),
        )

        # Remove old duplicates
        self.db.execute(
            "DELETE FROM events WHERE timestamp < ? AND is_duplicate = 1",
            (cutoff,),
        )

        self.db.commit()

        after_count = self.db.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]
        return before_count - after_count

    def purge_duplicates(self, dry_run: bool = False) -> int:
        """
        Collapse exact duplicate events across all time.

        Two events are exact duplicates when they share the same
        (type, data_hash). The newest row in each group is kept; older
        copies are hard-deleted. This addresses cross-window accumulation
        that emit()'s 60s dedup window cannot catch.

        Args:
            dry_run: If True, count what would be removed without deleting.

        Returns:
            Number of rows removed (or that would be removed if dry_run).
        """
        # Newest-first within each (type, data_hash) group.
        rows = self.db.execute(
            """
            SELECT id, type, data_hash
            FROM events
            ORDER BY type, data_hash, timestamp DESC, id DESC
            """
        ).fetchall()

        seen: set[tuple[str, str]] = set()
        to_delete: list[int] = []
        for r in rows:
            key = (r["type"], r["data_hash"])
            if key in seen:
                to_delete.append(r["id"])
            else:
                seen.add(key)

        if not dry_run and to_delete:
            self.db.executemany(
                "DELETE FROM events WHERE id = ?", [(i,) for i in to_delete]
            )
            self.db.commit()

        return len(to_delete)

    def mark_duplicate(self, event_id: int) -> bool:
        """Mark an event as duplicate (soft delete)."""
        cursor = self.db.execute(
            "UPDATE events SET is_duplicate = 1 WHERE id = ?",
            (event_id,),
        )
        self.db.commit()
        return cursor.rowcount > 0

    # ─── Stats ───────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return event store statistics."""
        total = self.db.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]
        duplicates = self.db.execute(
            "SELECT COUNT(*) as c FROM events WHERE is_duplicate = 1"
        ).fetchone()["c"]

        types = self.db.execute(
            "SELECT type, COUNT(*) as c FROM events GROUP BY type"
        ).fetchall()

        return {
            "total_events": total,
            "duplicates": duplicates,
            "unique_events": total - duplicates,
            "by_type": {r["type"]: r["c"] for r in types},
        }

    # ─── Internal ────────────────────────────────────────────────────

    def _row_to_event(self, row: sqlite3.Row) -> Event:
        """Convert a database row to an Event object."""
        data = {}
        try:
            data = json.loads(row["data"]) if row["data"] else {}
        except (json.JSONDecodeError, TypeError):
            data = {"raw": row["data"]}

        return Event(
            id=row["id"],
            type=row["type"],
            category=row["category"],
            data=data,
            priority=row["priority"],
            data_hash=row["data_hash"],
            project_dir=row["project_dir"],
            attribution_confidence=row["attribution_confidence"],
            timestamp=row["timestamp"],
            is_duplicate=bool(row["is_duplicate"]),
        )

    # ─── Lifecycle ───────────────────────────────────────────────────

    def close(self) -> None:
        """Close the database connection."""
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
