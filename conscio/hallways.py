"""Hallways — wing/room/drawer hierarchy.

Ported from MemPalace 3.6 hallways.py, simplified for Conscio:
- Removed dep on mempalace.config
- Auto-creates wing 'default' and room 'default' in __init__ (Protocol G fix)
- Default wing/room = 'default'/'default' when assign_drawer called without them

Schema:
    wings(name TEXT PK, created_at)
    rooms(name TEXT, wing TEXT FK, created_at, PK(wing, name))
    drawer_assignments(drawer_id INT, wing TEXT, room TEXT, assigned_at)
"""
from __future__ import annotations
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Hallways:
    """SQLite wing/room/drawer hierarchy with WAL persistence."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else Path.home() / ".conscio" / "runtime" / "hallways.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()
        # Auto-create default wing/room (Protocol G fix)
        self.create_wing("default")
        self.create_room("default", "default")

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
                CREATE TABLE IF NOT EXISTS wings (
                    name TEXT PRIMARY KEY,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS rooms (
                    wing TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (wing, name),
                    FOREIGN KEY (wing) REFERENCES wings(name)
                );

                CREATE TABLE IF NOT EXISTS drawer_assignments (
                    drawer_id INTEGER NOT NULL,
                    wing TEXT NOT NULL,
                    room TEXT NOT NULL,
                    assigned_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (drawer_id, wing, room),
                    FOREIGN KEY (wing, room) REFERENCES rooms(wing, name)
                );

                CREATE INDEX IF NOT EXISTS idx_drawers_wing ON drawer_assignments(wing);
                CREATE INDEX IF NOT EXISTS idx_drawers_wing_room ON drawer_assignments(wing, room);
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

    def __exit__(self, *a):
        self.close()
        return False

    # ── Wing/Room CRUD ───────────────────────────────────────────────

    def create_wing(self, name: str) -> None:
        with self._lock:
            conn = self._conn_get()
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO wings (name) VALUES (?)",
                    (name,),
                )

    def create_room(self, wing: str, name: str) -> None:
        # Ensure wing exists (auto-create to avoid FK violation)
        self.create_wing(wing)
        with self._lock:
            conn = self._conn_get()
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO rooms (wing, name) VALUES (?, ?)",
                    (wing, name),
                )

    def list_wings(self) -> list[str]:
        with self._lock:
            conn = self._conn_get()
            rows = conn.execute("SELECT name FROM wings ORDER BY name").fetchall()
        return [r[0] for r in rows]

    def list_rooms(self, wing: str) -> list[str]:
        with self._lock:
            conn = self._conn_get()
            rows = conn.execute(
                "SELECT name FROM rooms WHERE wing = ? ORDER BY name", (wing,)
            ).fetchall()
        return [r[0] for r in rows]

    # ── Drawer assignments ──────────────────────────────────────────

    def assign_drawer(
        self,
        wing: str = "default",
        room: str = "default",
        drawer_id: int | None = None,
    ) -> None:
        if drawer_id is None:
            raise ValueError("drawer_id required")
        self.create_room(wing, room)  # auto-create to avoid FK
        with self._lock:
            conn = self._conn_get()
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO drawer_assignments (drawer_id, wing, room) VALUES (?, ?, ?)",
                    (drawer_id, wing, room),
                )

    def list_drawers(
        self, wing: str | None = None, room: str | None = None
    ) -> list[int]:
        """List drawer_ids.

        If only wing given: all drawers in wing (across rooms).
        If wing + room: drawers in that specific room.
        If neither: all drawers.
        """
        with self._lock:
            conn = self._conn_get()
            if wing and room:
                rows = conn.execute(
                    "SELECT drawer_id FROM drawer_assignments WHERE wing = ? AND room = ?",
                    (wing, room),
                ).fetchall()
            elif wing:
                rows = conn.execute(
                    "SELECT drawer_id FROM drawer_assignments WHERE wing = ?",
                    (wing,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT drawer_id FROM drawer_assignments"
                ).fetchall()
        return [r[0] for r in rows]

    def remove_drawer(self, drawer_id: int) -> None:
        with self._lock:
            conn = self._conn_get()
            with conn:
                conn.execute(
                    "DELETE FROM drawer_assignments WHERE drawer_id = ?", (drawer_id,)
                )

    def stats(self) -> dict:
        with self._lock:
            conn = self._conn_get()
            wings = conn.execute("SELECT COUNT(*) FROM wings").fetchone()[0]
            rooms = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
            drawers = conn.execute("SELECT COUNT(*) FROM drawer_assignments").fetchone()[0]
        return {"wings": wings, "rooms": rooms, "drawers": drawers}

    def dump(self, target_path: str | Path) -> None:
        dst = sqlite3.connect(str(target_path))
        self._conn_get().backup(dst)
        dst.close()
