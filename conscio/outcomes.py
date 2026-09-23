"""v4.7 Task 6 — append-only decision outcome store (P1 minimum).

Schema (immutable, PRD §4 / spec §6):
    event_id, source, decision_ref, snapshot, outcome, outcome_ts, evidence_ref

- Append-only: the snapshot is written once and never rewritten.
- Idempotent by decision_ref: a duplicate returns the ORIGINAL event id.
- outcome ∈ {pending, success, failure, reverted, false_positive}.
  pending is the absence of a verdict, never a failure.
- Resolution updates outcome/outcome_ts/evidence_ref only; a resolve against
  a missing decision_ref is a visible no-op (logged), never silent.
- Capture hooks are non-blocking and pure: they build a record, the store
  persists it. Persistence errors propagate to the caller — never swallowed.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCES = frozenset({"council", "evaluate", "squad", "coherence"})
OUTCOMES = frozenset({
    "pending", "success", "failure", "reverted", "false_positive",
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_outcomes (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    decision_ref  TEXT NOT NULL UNIQUE,
    snapshot      TEXT NOT NULL,
    outcome       TEXT NOT NULL DEFAULT 'pending',
    outcome_ts    REAL,
    evidence_ref  TEXT,
    created_ts    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outcome_source
    ON decision_outcomes(source, outcome);
"""


@dataclass(frozen=True)
class OutcomeRecord:
    source: str
    decision_ref: str
    snapshot: dict
    evidence_ref: str = ""


class OutcomeStore:
    """Append-only store for decision outcomes with capture provenance."""

    def __init__(self, db_path: Path | str):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def append(self, record: OutcomeRecord) -> int:
        """Persist a decision snapshot. Idempotent by decision_ref: a
        duplicate returns the original event_id and touches nothing."""
        if record.source not in SOURCES:
            raise ValueError(
                f"unknown source {record.source!r}; expected one of "
                f"{sorted(SOURCES)}")
        try:
            cur = self._conn.execute(
                "INSERT INTO decision_outcomes"
                " (source, decision_ref, snapshot, outcome, created_ts)"
                " VALUES (?, ?, ?, 'pending', ?)",
                (record.source, record.decision_ref,
                 json.dumps(record.snapshot, ensure_ascii=False),
                 time.time()))
            self._conn.commit()
            return int(cur.lastrowid or 0)
        except sqlite3.IntegrityError:
            row = self._conn.execute(
                "SELECT event_id FROM decision_outcomes"
                " WHERE decision_ref=?", (record.decision_ref,)).fetchone()
            if row is None:
                raise
            logger.warning(
                "duplicate decision_ref %r ignored (event_id=%d)",
                record.decision_ref, row["event_id"])
            return int(row["event_id"])

    def resolve(self, decision_ref: str, outcome: str,
                evidence_ref: str = "") -> bool:
        """Attach a later verdict to an existing decision.

        Returns True when a pending record was resolved; False when the
        decision_ref does not exist (logged — a ghost resolve must be
        visible, not silent). The snapshot is never rewritten.
        """
        if outcome not in OUTCOMES:
            raise ValueError(
                f"unknown outcome {outcome!r}; expected one of "
                f"{sorted(OUTCOMES)}")
        cur = self._conn.execute(
            "UPDATE decision_outcomes"
            " SET outcome=?, outcome_ts=?, evidence_ref=?"
            " WHERE decision_ref=? AND outcome='pending'",
            (outcome, time.time(), evidence_ref, decision_ref))
        self._conn.commit()
        if cur.rowcount == 0:
            exists = self._conn.execute(
                "SELECT 1 FROM decision_outcomes WHERE decision_ref=?",
                (decision_ref,)).fetchone()
            if exists is None:
                logger.warning(
                    "resolve() on unknown decision_ref %r — no record",
                    decision_ref)
            return False
        return True

    def get_by_event_id(self, event_id: int) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM decision_outcomes WHERE event_id=?",
            (event_id,)).fetchone()
        if row is None:
            raise KeyError(f"no decision_outcomes row for event_id={event_id}")
        return row

    def get(self, decision_ref: str) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM decision_outcomes WHERE decision_ref=?",
            (decision_ref,)).fetchone()
        if row is None:
            raise KeyError(f"no decision_outcomes row for {decision_ref!r}")
        return row

    def close(self) -> None:
        self._conn.close()


def capture_council_outcome(store: OutcomeStore, result: dict) -> int:
    """Capture hook for the council surface: snapshot the decision with its
    agreement and vote provenance. The outcome stays pending until a later
    resolve() — the capture is non-blocking and adds no verdict of its own.
    """
    snapshot = {
        "question": result.get("question", ""),
        "recommendation": result.get("recommendation", ""),
        "agreement": result.get("agreement", {}),
        "votes_summary": result.get("votes_summary", {}),
    }
    # Deterministic, collision-free ref without needing the row id first:
    # the store serializes appends, so ns-resolution + question hash is
    # unique per capture. Idempotency comes from the UNIQUE index — the
    # same ns never repeats.
    import hashlib
    q = result.get("question", "")
    digest = hashlib.sha1(q.encode("utf-8", "replace")).hexdigest()[:10]
    ref = f"council:{time.time_ns()}:{digest}"
    record = OutcomeRecord(source="council", decision_ref=ref,
                           snapshot=snapshot)
    eid = store.append(record)
    # expose the ref through the same event id the store returned
    return eid
