"""Engine-free read-only projection of one instance's persisted state.

Opens conscio.db with mode=ro (NO PRAGMA, SELECT only) for events/actions/skills
and parses two JSON state files (goals.json is a LIST; state_summary.json is an
OBJECT). Never writes. Source-pluggable: a v2.5 noosphere source adds its own
method group + path without touching the conscio.db / JSON paths here."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..guards import clamp_int, safe_read_json   # leaf utils; not conscio.engine


def _read_json_list(path: Path) -> list[dict]:
    """safe_read_json is dict-only; goals.json is a list. Read a JSON list
    safely: any problem (missing, OSError, malformed, not-a-list) -> []."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


class Projection:
    def __init__(self, storage: Path) -> None:
        self.storage = Path(storage)
        self._db = self.storage / "conscio.db"

    # ── conscio.db (read-only) ──
    def _ro(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self._db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn                               # NO PRAGMA — mode=ro blocks writes

    def _select(self, sql: str, params: list[Any]) -> list[dict]:
        if not self._db.exists():
            return []
        try:
            conn = self._ro()
        except sqlite3.OperationalError:
            return []
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []                             # table absent on a partial db
        finally:
            conn.close()

    def events(self, *, type: str | None = None, category: str | None = None,
               since: str | None = None, limit: int = 50) -> list[dict]:
        conds, params = [], []
        if type:
            conds.append("type = ?"); params.append(type)
        if category:
            conds.append("category = ?"); params.append(category)
        if since:
            conds.append("timestamp >= ?"); params.append(since)   # shares fate w/ event_bus.query
        where = " AND ".join(conds) if conds else "1=1"
        rows = self._select(
            "SELECT id, type, category, data, priority, data_hash, project_dir,"
            " attribution_confidence, timestamp, is_duplicate FROM events"
            f" WHERE {where} ORDER BY timestamp DESC LIMIT ?",
            params + [clamp_int(limit, 1, 500)])
        for r in rows:
            try:
                r["data"] = json.loads(r.get("data") or "{}")
            except (ValueError, TypeError):
                r["data"] = {}
            r["is_duplicate"] = bool(r.get("is_duplicate"))
        return rows

    def actions(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            return self._select(
                "SELECT * FROM actions WHERE status = ? ORDER BY id DESC LIMIT ?",
                [status, clamp_int(limit, 1, 500)])
        return self._select(
            "SELECT * FROM actions ORDER BY id DESC LIMIT ?",
            [clamp_int(limit, 1, 500)])

    def skills(self, *, limit: int = 100) -> list[dict]:
        return self._select(
            "SELECT * FROM skills ORDER BY id DESC LIMIT ?",
            [clamp_int(limit, 1, 500)])

    # ── JSON state files ──
    def goals(self) -> list[dict]:
        return _read_json_list(self.storage / "goals.json")  # NOT safe_read_json

    def state(self) -> dict:
        return safe_read_json(self.storage / "state_summary.json") or {}
