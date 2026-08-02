"""CompactionCheckpoint + CheckpointChain (v3.1 Ato 2).

Checkpoints are append-only durable rows. Each checkpoint captures 4 artifacts
(durable_memory, execution_summary, user_requirements, skill_references) and
links to its parent via parent_id. The chain never rewrites — a new prompt
reconstructed from the latest checkpoint becomes a new cacheable prefix.

Mirrors mechanism 2 (structured, incremental, cache-aware compaction) from
The Harness Effect paper, Section 4.2.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CompactionCheckpoint:
    """Immutable snapshot of conversation state at compaction time."""
    durable_memory: str           # decisions, constraints, rejected approaches
    execution_summary: str        # 8-section summary for resumability
    user_requirements: str         # preserved verbatim
    skill_references: list[str]     # skill names for progressive disclosure

    @property
    def byte_hash(self) -> str:
        """SHA-256 of all artifacts — detect content drift.

        skill_references are sorted before hashing so that the same
        set of skills produces the same hash regardless of insertion order.
        """
        payload = json.dumps({
            "d": self.durable_memory,
            "e": self.execution_summary,
            "u": self.user_requirements,
            "s": sorted(self.skill_references),
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "durable_memory": self.durable_memory,
            "execution_summary": self.execution_summary,
            "user_requirements": self.user_requirements,
            "skill_references": json.dumps(self.skill_references),
            "byte_hash": self.byte_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CompactionCheckpoint:
        return cls(
            durable_memory=d["durable_memory"],
            execution_summary=d["execution_summary"],
            user_requirements=d["user_requirements"],
            skill_references=json.loads(d.get("skill_references", "[]")),
        )


class CheckpointChain:
    """Append-only chain of CompactionCheckpoints backed by SQLite.

    Never rewrites. Each append links to the previous latest.
    Periodically consolidates old entries to bound chain length.
    """

    def __init__(self, db_path: str | Path, *, consolidate_every: int = 0):
        self.db_path = Path(db_path)
        self.consolidate_every = consolidate_every
        self._init_db()

    def _connect(self) -> closing[sqlite3.Connection]:
        """Open a connection that closes even when the body raises.

        v3.9.4: every method here used a bare connect/…/close pair. A raise in
        between leaves the handle to the traceback, and the exception →
        traceback → frame cycle means only the *cyclic* collector frees it —
        refcounting alone does not. Measured: 30 failed appends held 30
        descriptors open with the GC disabled. NOT `with sqlite3.connect(...)`:
        that context manager commits or rolls back the transaction and leaves
        the connection open.

        `isolation_level=None` turns off the driver's implicit BEGIN, so every
        transaction boundary in this module is one written here.
        """
        return closing(sqlite3.connect(str(self.db_path), isolation_level=None))

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER,
                    durable_memory TEXT NOT NULL,
                    execution_summary TEXT NOT NULL,
                    user_requirements TEXT NOT NULL,
                    skill_references TEXT NOT NULL,
                    byte_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)

    def append(self, cp: CompactionCheckpoint) -> int:
        """Append a checkpoint to the chain. Returns checkpoint_id."""
        d = cp.to_dict()
        with self._connect() as conn:
            # v3.9.4: take the write lock BEFORE reading the parent, so the
            # whole read-modify-write is one step for every process sharing this
            # file. Without it two appends read the same latest row and both
            # claim it as parent — measured with 4 processes × 25 appends, 4 of
            # 5 trials forked the chain and one produced three roots. Late
            # arrivals wait out sqlite's busy timeout instead of racing.
            conn.execute("BEGIN IMMEDIATE")
            try:
                latest = self._latest_row(conn)
                parent_id = latest["checkpoint_id"] if latest else None

                cur = conn.execute(
                    """INSERT INTO checkpoints
                       (parent_id, durable_memory, execution_summary,
                        user_requirements, skill_references, byte_hash, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (parent_id, d["durable_memory"], d["execution_summary"],
                     d["user_requirements"], d["skill_references"], d["byte_hash"],
                     time.time()),
                )
                cid = cur.lastrowid or 0

                if self.consolidate_every and self._count(conn) >= self.consolidate_every * 2:
                    self._consolidate(conn)

                conn.commit()
            except BaseException:
                conn.rollback()
                raise

            return cid

    def latest(self) -> dict | None:
        """Return the most recent checkpoint as dict with metadata, or None."""
        with self._connect() as conn:
            return self._latest_row(conn)

    def get(self, checkpoint_id: int) -> dict | None:
        """Retrieve a checkpoint by ID as dict with metadata."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def length(self) -> int:
        """Number of checkpoints in the chain."""
        with self._connect() as conn:
            return self._count(conn)

    @staticmethod
    def _count(conn: sqlite3.Connection) -> int:
        """Row count on an EXISTING connection — an append must not open a second
        one mid-transaction, or it would count without seeing its own insert."""
        return conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]

    def _latest_row(self, conn: sqlite3.Connection) -> dict | None:
        cur = conn.execute(
            "SELECT * FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def _row_to_dict(self, row) -> dict:
        return {
            "checkpoint_id": row[0],
            "parent_id": row[1],
            "durable_memory": row[2],
            "execution_summary": row[3],
            "user_requirements": row[4],
            "skill_references": row[5],
            "byte_hash": row[6],
            "created_at": row[7],
        }

    def _consolidate(self, conn: sqlite3.Connection) -> None:
        """Merge oldest checkpoints into a single summary checkpoint.

        Keeps the latest `consolidate_every` entries intact.
        Older entries are replaced by one consolidated checkpoint
        that preserves the durable_memory and user_requirements from
        the oldest, and the execution_summary from the newest of the
        consolidated range.

        Runs inside the caller's transaction and does not commit: the delete,
        the merged insert and the re-anchor are one step or none of them are.
        """
        cur = conn.execute(
            "SELECT * FROM checkpoints ORDER BY checkpoint_id"
        )
        rows = cur.fetchall()
        if len(rows) <= self.consolidate_every:
            return

        keep_count = self.consolidate_every
        to_merge = rows[:-keep_count]

        if not to_merge:
            return

        first = self._row_to_dict(to_merge[0])
        last = self._row_to_dict(to_merge[-1])

        # Merge: preserve user_requirements from first, execution_summary from last,
        # concatenate durable_memory.
        merged_cp = CompactionCheckpoint(
            durable_memory=first["durable_memory"] + "\n---\n" + last["durable_memory"],
            execution_summary=last["execution_summary"],
            user_requirements=first["user_requirements"],
            skill_references=json.loads(last.get("skill_references", "[]")),
        )

        # Delete old rows, insert merged
        ids_to_delete = [r[0] for r in to_merge]
        placeholders = ",".join("?" * len(ids_to_delete))
        conn.execute(
            f"DELETE FROM checkpoints WHERE checkpoint_id IN ({placeholders})",
            ids_to_delete,
        )

        d = merged_cp.to_dict()
        first_id = first["checkpoint_id"]
        # Update first row with merged content
        conn.execute(
            """INSERT INTO checkpoints
               (checkpoint_id, parent_id, durable_memory, execution_summary,
                user_requirements, skill_references, byte_hash, created_at)
               VALUES (?, NULL, ?, ?, ?, ?, ?, ?)""",
            (first_id, d["durable_memory"], d["execution_summary"],
             d["user_requirements"], d["skill_references"], d["byte_hash"],
             time.time()),
        )

        # v3.9.4: the merged range is gone, so the first surviving checkpoint
        # now points at a parent that no longer exists. Re-anchor it to the
        # merged row — that row IS its history, and a chain you cannot walk
        # back is not a chain.
        conn.execute(
            "UPDATE checkpoints SET parent_id = ? WHERE checkpoint_id = ?",
            (first_id, rows[-keep_count][0]),
        )
