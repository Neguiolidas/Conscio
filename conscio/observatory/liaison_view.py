# conscio/observatory/liaison_view.py
"""Engine-free read-only projection of this instance's liaison inbox.

Opens liaison.db with ?mode=ro (NO PRAGMA, SELECT only) over the `messages`
table. CANNOT reuse conscio.liaison.mailbox.inbox() — it opens read-write
(PRAGMA journal_mode=WAL + executescript), which would violate the Observatory's
read-only contract. mode=ro reads the latest committed WAL rows (immutable=1
rejected, same reason as society.py). Never marks anything read — it is a viewer.
Shows the FULL payload (private loopback inbox, unlike the public Society view)."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from ..guards import clamp_int   # leaf util; not conscio.engine

log = logging.getLogger(__name__)


class LiaisonProjection:
    def __init__(self, liaison_db: Path) -> None:
        self.db = Path(liaison_db)            # public: surfaced in /api/health

    def _ro(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn                           # NO PRAGMA — mode=ro blocks writes

    def inbox(self, self_id: str, *, limit: int = 50) -> list[dict]:
        if not self_id or not self.db.exists():
            return []
        try:
            conn = self._ro()
        except sqlite3.OperationalError:
            return []
        try:
            rows = conn.execute(
                "SELECT id, from_instance, to_instance, type, payload, ts, read_ts"
                " FROM messages WHERE to_instance=? ORDER BY id DESC LIMIT ?",
                [self_id, clamp_int(limit, 1, 200)]).fetchall()
        except sqlite3.OperationalError:
            return []                         # table absent on a partial db
        finally:
            conn.close()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"])
            except (TypeError, ValueError):
                log.warning("skipping liaison row %s: unparseable payload",
                            d.get("id"))
                continue                      # unparseable row -> skip (R1: logged)
            out.append(d)
        return out
