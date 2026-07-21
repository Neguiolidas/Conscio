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
        """SHA-256 of all artifacts — detect content drift."""
        payload = json.dumps({
            "d": self.durable_memory,
            "e": self.execution_summary,
            "u": self.user_requirements,
            "s": self.skill_references,
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

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
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
        conn.commit()
        conn.close()

    def append(self, cp: CompactionCheckpoint) -> int:
        """Append a checkpoint to the chain. Returns checkpoint_id."""
        conn = sqlite3.connect(str(self.db_path))
        latest = self._latest_row(conn)
        parent_id = latest["checkpoint_id"] if latest else None

        d = cp.to_dict()
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
        conn.commit()

        if self.consolidate_every and self.length() >= self.consolidate_every * 2:
            self._consolidate(conn)

        conn.close()
        return cid

    def latest(self) -> dict | None:
        """Return the most recent checkpoint as dict with metadata, or None."""
        conn = sqlite3.connect(str(self.db_path))
        row = self._latest_row(conn)
        conn.close()
        return row

    def get(self, checkpoint_id: int) -> dict | None:
        """Retrieve a checkpoint by ID as dict with metadata."""
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute(
            "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        )
        row = cur.fetchone()
        conn.close()
        if row is None:
            return None
        return self._row_to_dict(row)

    def length(self) -> int:
        """Number of checkpoints in the chain."""
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute("SELECT COUNT(*) FROM checkpoints")
        count = cur.fetchone()[0]
        conn.close()
        return count

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
        conn.commit()
