# conscio/observatory/society.py
"""Engine-free read-only projection of the host-shared noosphere (the "society").

Opens noosphere.db with mode=ro (NO PRAGMA, SELECT only) over published_skills
and published_records. Never writes; never reuses the noosphere catalog writers
(catalog.py / record_catalog.py open read-write and run executescript DDL).
mode=ro reads the latest committed WAL rows (immutable=1 deliberately rejected —
it ignores -wal and would return stale/empty data under a concurrent writer)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..guards import clamp_int   # leaf util; not conscio.engine


class SocietyProjection:
    def __init__(self, noosphere_db: Path) -> None:
        self.db = Path(noosphere_db)            # public: surfaced in /api/health

    def _ro(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn                             # NO PRAGMA — mode=ro blocks content writes

    def _select(self, sql: str, params: list[Any]) -> list[dict]:
        if not self.db.exists():
            return []
        try:
            conn = self._ro()
        except sqlite3.OperationalError:
            return []
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []                           # table absent on a partial db
        finally:
            conn.close()

    def skills(self, *, limit: int = 100) -> list[dict]:
        # BLOBs (plan_template, artifact_json) deliberately omitted (metadata-only)
        return self._select(
            "SELECT origin_instance_id, origin_label, goal_fp, goal_text,"
            " tool_seq, published_ts, content_sha256 FROM published_skills"
            " ORDER BY published_ts DESC LIMIT ?",
            [clamp_int(limit, 1, 500)])

    def records(self, *, limit: int = 100) -> list[dict]:
        # bundle_json BLOB deliberately omitted (metadata-only)
        return self._select(
            "SELECT origin_instance_id, origin_label, published_ts, content_sha256,"
            " entry_count, window_first_ts, window_last_ts FROM published_records"
            " ORDER BY published_ts DESC LIMIT ?",
            [clamp_int(limit, 1, 500)])

    def members(self) -> list[dict]:
        # census = union of distinct instances across both tables, with counts.
        # GROUP BY origin_instance_id alone (origin_label not aggregated): label is
        # functionally dependent on instance_id in the noosphere model, so SQLite's
        # bare-column rule returns the right label. Not standard SQL — intentional.
        skills = self._select(
            "SELECT origin_instance_id, origin_label, COUNT(*) AS n,"
            " MAX(published_ts) AS last FROM published_skills"
            " GROUP BY origin_instance_id", [])
        records = self._select(
            "SELECT origin_instance_id, origin_label, COUNT(*) AS n,"
            " MAX(published_ts) AS last FROM published_records"
            " GROUP BY origin_instance_id", [])
        members: dict[str, dict] = {}
        for rows, key in ((skills, "skills_count"), (records, "records_count")):
            for r in rows:
                m = members.setdefault(r["origin_instance_id"], {
                    "origin_instance_id": r["origin_instance_id"],
                    "origin_label": r["origin_label"], "skills_count": 0,
                    "records_count": 0, "last_published_ts": 0.0})
                m[key] = r["n"]
                m["origin_label"] = r["origin_label"]
                if (r["last"] or 0) > m["last_published_ts"]:
                    m["last_published_ts"] = r["last"]
        return sorted(members.values(),
                      key=lambda m: m["last_published_ts"], reverse=True)
