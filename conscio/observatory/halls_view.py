# conscio/observatory/halls_view.py
"""Engine-free read-only projection of agents + halls for the Observatory.

Mirror of `society.py` / `liaison_view.py`: opens liaison.db with mode=ro
(NO PRAGMA, SELECT only), never marks anything read, never writes. Reads the
latest committed WAL rows. See the agents registry (`agents` table) and the
Agent's Hall (`halls` + `hall_members` tables) side by side.

Read-only contract: `_ro` uses mode=ro; a missing/corrupt db or absent table
degrades to [] (never raises). No write path here.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..guards import clamp_int  # leaf util; not conscio.engine


class HallsProjection:
    def __init__(self, liaison_db: Path) -> None:
        self.db = Path(liaison_db)

    def _ro(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _select(self, sql: str, params: list) -> list[dict]:
        if not self.db.exists():
            return []
        try:
            conn = self._ro()
        except sqlite3.OperationalError:
            return []
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    def agents(self, *, include_stale: bool = False,
               limit: int = 200) -> list[dict]:
        """Agent registry rows (identity + presence). Stale excluded by
        default; when include_stale, each row carries `offline: True` so the
        viewer can dim it out instead of hiding it."""
        rows = self._select(
            "SELECT instance_id, model, status, capabilities, last_heartbeat,"
            " nome, familia, runtime, papel FROM agents ORDER BY last_heartbeat"
            f" DESC LIMIT {clamp_int(limit, 1, 500)}", [])
        out: list[dict] = []
        from ..liaison import agents as _agents
        now = time.time()
        for r in rows:
            r["capabilities"] = _agents._parse_caps(r.get("capabilities", ""))
            offline = (now - float(r.get("last_heartbeat", 0) or 0)) \
                > _agents.STALE_AFTER_S
            r["offline"] = offline
            if include_stale or not offline:
                out.append(r)
        return out

    def halls(self, *, dono: str | None = None) -> list[dict]:
        """Halls with member counts, newest first."""
        if dono:
            rows = self._select(
                "SELECT hall_id, nome, dono, criado_em,"
                " (SELECT COUNT(*) FROM hall_members m WHERE m.hall_id=h.hall_id)"
                " AS member_count FROM halls h WHERE dono=? ORDER BY criado_em DESC",
                [dono])
        else:
            rows = self._select(
                "SELECT hall_id, nome, dono, criado_em,"
                " (SELECT COUNT(*) FROM hall_members m WHERE m.hall_id=h.hall_id)"
                " AS member_count FROM halls h ORDER BY criado_em DESC", [])
        return rows

    def hall_members(self, hall_id: str, *,
                     alive_only: bool = True,
                     limit: int = 100) -> list[dict]:
        """Members of a hall, joined with registry identity (modelo/familia)."""
        rows = self._select(
            "SELECT m.hall_id, m.instance_id, m.papel, m.entrou_em,"
            " a.model, a.familia, a.status, a.last_heartbeat"
            " FROM hall_members m LEFT JOIN agents a"
            " ON a.instance_id = m.instance_id WHERE m.hall_id=?"
            f" ORDER BY m.entrou_em DESC LIMIT {clamp_int(limit, 1, 200)}",
            [hall_id])
        from ..liaison import agents as _agents
        now = time.time()
        out: list[dict] = []
        for r in rows:
            hb = float(r.get("last_heartbeat") or 0)
            offline = hb and (now - hb) > _agents.STALE_AFTER_S
            r["modelo"] = r.pop("model", "") or ""
            r["offline"] = offline
            if alive_only and offline:
                continue
            out.append(r)
        return out

    def mailboxes(self, self_id: str, *, limit: int = 200) -> list[dict]:
        """Per-peer unread directed counts addressed to `self_id`."""
        rows = self._select(
            "SELECT from_instance, COUNT(*) AS unread FROM messages"
            " WHERE to_instance=? AND read_ts IS NULL"
            " GROUP BY from_instance ORDER BY unread DESC"
            f" LIMIT {clamp_int(limit, 1, 500)}", [self_id])
        return rows