# conscio/perception/relay_sensor.py
"""RelaySensor — read-only perception of the liaison inbox (v2.6.2 "Awake
Relay Sensor"). Reports unread directed messages so an Awake engine KNOWS its
peers are talking; it NEVER marks read — consumption stays on the tool/act path
(relay_inbox/relay_read or apply_verdicts). Tolerant of a missing/corrupt db
(degrades to a reduced frame), like every reference sensor. stdlib only."""
from __future__ import annotations

import time

from ..liaison import mailbox
from ..liaison.relay import RESERVED_TYPES
from ..risk import Risk
from .sensor import PerceptionFrame, SensorAdapter


class RelaySensor(SensorAdapter):
    """Unread liaison inbox as a `PerceptionFrame` (source ``"relay"``)."""

    name = "relay"
    risk = Risk.LOW

    def __init__(self, liaison_db, self_id: str, peers, *,
                 limit: int = 50) -> None:
        self.liaison_db = liaison_db
        self.self_id = self_id
        self.peers = frozenset(peers)
        self.limit = limit

    def perceive(self) -> PerceptionFrame:
        obs: list[str] = []
        relay_from: dict[str, int] = {}
        review_from: dict[str, int] = {}
        other_n = 0
        rows = mailbox.inbox(self.liaison_db, self.self_id,
                             unread_only=True, limit=self.limit)  # read-only
        for m in rows:
            frm = m.get("from_instance", "")
            typ = m.get("type", "")
            if typ == "review_verdict" and frm in self.peers:
                review_from[frm] = review_from.get(frm, 0) + 1
            elif typ in RESERVED_TYPES:
                continue                       # review_request etc.: not surfaced
            elif frm in self.peers:
                relay_from[frm] = relay_from.get(frm, 0) + 1
            else:
                other_n += 1                   # non-peer: counted, not detailed
        for frm, n in sorted(relay_from.items()):
            obs.append(f"relay: {n} unread from {frm[:8]}")
        for frm, n in sorted(review_from.items()):
            obs.append(f"review: {n} verdict(s) pending from {frm[:8]}")
        if other_n:
            obs.append(f"relay: {other_n} unread from non-peers (ignored)")
        if not obs:
            obs.append("relay: inbox quiet")
        signals = {"relay_unread": float(sum(relay_from.values())),
                   "review_pending": float(sum(review_from.values()))}
        return PerceptionFrame(source="relay", observations=obs,
                               signals=signals, ts=time.time())
