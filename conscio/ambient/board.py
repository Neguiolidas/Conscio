# conscio/ambient/board.py
"""Conscio Ambient board (v4.8 S3): the task state of one machine.

stdlib-only and engine-free (tests/test_ambient_engine_free.py). One board
per relay_root (I1), at <relay_root>/ambient/board.db. Every write runs in
BEGIN IMMEDIATE and records its board_events row in the SAME transaction
(R6): the report reads only board_events, so a write without its event is a
write the report cannot see.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000
DEFAULT_LEASE_S = 3600
MAX_ATTEMPTS = 2
SAMPLE_KEEP = 36
RETENTION_DAYS = 90
TERMINAL_STATES = ("done", "cancelled")
GATE_KINDS = ("no_connector", "budget_exhausted", "admission_denied",
              "liveness_unknown", "agent_live", "concurrency")

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, body TEXT NOT NULL,
        state TEXT NOT NULL,
        creator TEXT NOT NULL, assignee TEXT, reviewer TEXT,
        fence INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        lease_expires_ts REAL,
        resumable TEXT,
        origin TEXT UNIQUE,
        created_ts REAL NOT NULL, updated_ts REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS task_files (
        task_id INTEGER NOT NULL REFERENCES tasks(id),
        path TEXT NOT NULL,
        reserved INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (task_id, path))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS one_holder_per_path
        ON task_files(path) WHERE reserved = 1""",
    """CREATE TABLE IF NOT EXISTS board_lease (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        holder TEXT NOT NULL, fence INTEGER NOT NULL,
        acquired_ts REAL NOT NULL, expires_ts REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL,
        task_id INTEGER, connector TEXT NOT NULL, pid INTEGER,
        started_ts REAL NOT NULL, state TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS admission_samples (
        ts REAL PRIMARY KEY, load1 REAL NOT NULL,
        mem_available_mb INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS board_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL, kind TEXT NOT NULL, task_id INTEGER,
        actor TEXT, payload TEXT NOT NULL)""",
)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class BoardError(Exception):
    """Every refusal of the board. str(exc) is the exact product text (§10)."""


class ClaimLost(BoardError):
    def __init__(self, task_id: int) -> None:
        super().__init__(f"ClaimLost (task {task_id})")


class NotAssignee(BoardError):
    def __init__(self, task_id: int, assignee: str | None) -> None:
        super().__init__(f"NotAssignee (task {task_id}, assignee {assignee})")


class NotClaimable(BoardError):
    def __init__(self, task_id: int, state: str) -> None:
        super().__init__(f"NotClaimable (task {task_id}, state {state})")


class FilesReserved(BoardError):
    def __init__(self, task_id: int, path: str, held_by: int) -> None:
        super().__init__(
            f"FilesReserved (task {task_id}, path {path}, held by task {held_by})")


class RelativePath(BoardError):
    def __init__(self, path: str) -> None:
        super().__init__(f"RelativePath ({path})")


class StaleFence(BoardError):
    """task_id None means the orchestration lease, not a task."""

    def __init__(self, task_id: int | None, fence: int, current: int) -> None:
        subject = "orchestration" if task_id is None else f"task {task_id}"
        super().__init__(f"StaleFence ({subject}, fence {fence}, current {current})")


class NoOrchestrationLease(BoardError):
    def __init__(self) -> None:
        super().__init__("NoOrchestrationLease (backlog frozen; "
                         "claim existing tasks or take the lease)")


class OrchestrationHeld(BoardError):
    def __init__(self, holder: str, expires_ts: float) -> None:
        super().__init__(
            f"OrchestrationHeld (holder {holder}, expires {_iso(expires_ts)})")


class SelfReview(BoardError):
    def __init__(self, task_id: int) -> None:
        super().__init__(f"SelfReview (task {task_id})")


class BoardBusy(BoardError):
    def __init__(self) -> None:
        super().__init__("BoardBusy (retry)")


class BoardTooNew(BoardError):
    def __init__(self, file_version: int, code_version: int) -> None:
        super().__init__(f"BoardTooNew (file v{file_version}, code v{code_version})")


class NoSuchTask(BoardError):
    def __init__(self, task_id: int) -> None:
        super().__init__(f"NoSuchTask (task {task_id})")


class NotReviewer(BoardError):
    def __init__(self, task_id: int, reviewer: str | None) -> None:
        super().__init__(f"NotReviewer (task {task_id}, reviewer {reviewer})")


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


@contextmanager
def _tx(db: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE .. COMMIT. `database is locked` never leaves raw:
    past busy_timeout it becomes BoardBusy (§6.2)."""
    try:
        db.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if _is_busy(exc):
            raise BoardBusy() from None
        raise
    try:
        yield db
        db.execute("COMMIT")
    except BaseException as exc:
        if db.in_transaction:
            db.execute("ROLLBACK")
        if isinstance(exc, sqlite3.OperationalError) and _is_busy(exc):
            raise BoardBusy() from None
        raise


@contextmanager
def _reading() -> Iterator[None]:
    try:
        yield
    except sqlite3.OperationalError as exc:
        if _is_busy(exc):
            raise BoardBusy() from None
        raise


def open_board(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open the board, creating it when absent, and check its schema.

    A file written by NEWER code raises BoardTooNew and stays untouched:
    reactors of different versions share this file (seen with 4.7.2/4.7.3),
    and an older one must not write a schema it does not know (§7.1).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(p), timeout=BUSY_TIMEOUT_MS / 1000,
                         isolation_level=None)
    try:
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_MS)}")
        with _reading():
            found = int(db.execute("PRAGMA user_version").fetchone()[0])
            if found > SCHEMA_VERSION:
                raise BoardTooNew(found, SCHEMA_VERSION)
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA foreign_keys = ON")
        if found < SCHEMA_VERSION:
            with _tx(db):
                for stmt in _SCHEMA:
                    db.execute(stmt)
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    except BaseException:
        db.close()
        raise
    return db


def _event(db: sqlite3.Connection, kind: str, task_id: int | None,
           actor: str | None, payload: dict, now: float) -> None:
    db.execute(
        "INSERT INTO board_events (ts, kind, task_id, actor, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        (now, kind, task_id, actor,
         json.dumps(payload, sort_keys=True, ensure_ascii=False)))


def _task(db: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (int(task_id),)).fetchone()
    if row is None:
        raise NoSuchTask(int(task_id))
    return row
