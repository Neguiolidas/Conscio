# conscio/observatory/halls_view.py
"""Engine-free read-only projection of agents + halls for the Observatory.

Mirror of `society.py` / `liaison_view.py`: opens liaison.db with mode=ro
(NO PRAGMA, SELECT only), never marks anything read, never writes. Reads the
latest committed WAL rows. Two sources, on purpose: the agents registry and the
mailboxes come from the private `liaison.db`; halls and their membership come
from the public relay directory (v4.5.4 — there is no shared roster table).

Read-only contract: `_ro` uses mode=ro; a missing/corrupt db or absent table
degrades to [] (never raises). The directory side is equally read-only: it
only calls `directory.peers` / `halls.list_halls` / `halls.members_of`.
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
        viewer can dim it out instead of hiding it.

        Delegates to `agents.list_agents`: single source of truth for the
        identity-column SELECT (`_identity_select` probes PRAGMA so a legacy
        db without nome/familia/runtime/papel still reads, never hardcodes).
        """
        from ..liaison import agents as _agents
        rows = _agents.list_agents(self.db, include_stale=include_stale)
        now = time.time()
        out: list[dict] = []
        for r in rows[:clamp_int(limit, 1, 500)]:
            r = dict(r)
            offline = (now - float(r.get("last_heartbeat", 0) or 0)) \
                > _agents.STALE_AFTER_S
            r["offline"] = offline
            if include_stale or not offline:
                out.append(r)
        return out

    def halls(self, *, owner: str | None = None) -> list[dict]:
        """Halls with member counts, newest first. Roster mora no diretório
        (v4.5.4): uma varredura só para todos os halls, não uma por hall."""
        from ..liaison import directory
        from ..liaison import halls as _halls
        counts: dict[str, int] = {}
        for card in directory.peers():
            declined = set(card.get("halls_declined") or [])
            for hid in (card.get("halls") or []):
                if hid not in declined:
                    counts[hid] = counts.get(hid, 0) + 1
        out = []
        for doc in _halls.list_halls(owner=owner):
            doc = dict(doc)
            # quem o diretório enxerga; importado sem cartão não entra na conta
            doc["member_count"] = counts.get(doc["hall_id"], 0)
            out.append(doc)
        return out

    def hall_members(self, hall_id: str, *,
                     alive_only: bool = True,
                     limit: int = 100) -> list[dict]:
        """Members of a hall, straight from the public directory."""
        from ..liaison import halls as _halls
        out: list[dict] = []
        for m in _halls.members_of(hall_id):
            m = dict(m)
            m["offline"] = not m.pop("alive", False)
            if alive_only and m["offline"]:
                continue
            out.append(m)
        return out[:clamp_int(limit, 1, 200)]

    def mailboxes(self, self_id: str, *, limit: int = 200) -> list[dict]:
        """Per-peer unread directed counts addressed to `self_id`."""
        rows = self._select(
            "SELECT from_instance, COUNT(*) AS unread FROM messages"
            " WHERE to_instance=? AND read_ts IS NULL"
            " GROUP BY from_instance ORDER BY unread DESC"
            f" LIMIT {clamp_int(limit, 1, 500)}", [self_id])
        return rows