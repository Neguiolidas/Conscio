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
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000
DEFAULT_LEASE_S = 3600
MAX_ATTEMPTS = 2
SAMPLE_KEEP = 36
RETENTION_DAYS = 90
TERMINAL_STATES = ("done", "cancelled")

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


def _set_wal_mode(db: sqlite3.Connection) -> None:
    """Set WAL journal mode, retrying on busy/locked up to BUSY_TIMEOUT_MS.

    SQLite deadlock avoidance returns BUSY/locked immediately (0-40 ms)
    when upgrading to WAL if another connection holds a SHARED lock,
    bypassing the busy handler. We retry with short pauses until the
    deadline is reached.
    """
    deadline = time.monotonic() + (BUSY_TIMEOUT_MS / 1000.0)
    pause = 0.005
    while True:
        try:
            db.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if not _is_busy(exc):
                raise
            if time.monotonic() >= deadline:
                raise BoardBusy() from None
            time.sleep(pause)
            pause = min(pause * 1.5, 0.050)


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
            _set_wal_mode(db)
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


def _declare(files: Iterable[str]) -> list[str]:
    """Absolute, resolved, deduplicated. The key of a reservation is the
    resolved path, so a symlink COLLIDES with its target (measured
    2026-09-25) and a hardlink does not (known limit, §6.5)."""
    out: list[str] = []
    for raw in files:
        if not isinstance(raw, str):
            raise ValueError(f"file path must be a string, got {type(raw).__name__}")
        expanded = os.path.expanduser(raw)
        if not os.path.isabs(expanded):
            raise RelativePath(raw)
        resolved = str(Path(expanded).resolve())
        if resolved not in out:
            out.append(resolved)
    return out


def _insert_task(db: sqlite3.Connection, *, title: str, body: str, state: str,
                 creator: str, assignee: str | None, reviewer: str | None,
                 origin: str | None, now: float) -> int:
    cur = db.execute(
        "INSERT INTO tasks (title, body, state, creator, assignee, reviewer,"
        " origin, created_ts, updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(title), str(body), state, creator, assignee, reviewer, origin, now, now))
    if cur.lastrowid is None:
        raise RuntimeError("insert without rowid")
    return int(cur.lastrowid)


def _insert_files(db: sqlite3.Connection, task_id: int, paths: list[str]) -> None:
    db.executemany("INSERT INTO task_files (task_id, path) VALUES (?, ?)",
                   [(task_id, p) for p in paths])


def _lease_row(db: sqlite3.Connection) -> sqlite3.Row | None:
    return db.execute("SELECT holder, fence, acquired_ts, expires_ts "
                      "FROM board_lease WHERE id = 1").fetchone()


def _require_orch(db: sqlite3.Connection, *, orch_fence: int, now: float,
                  holder: str | None = None) -> str:
    """The dispatch check of §6.4. Passing it RENEWS the lease (every
    dispatch write of the holder does). Returns the holder."""
    row = _lease_row(db)
    if row is None or row["expires_ts"] <= now:
        raise NoOrchestrationLease()
    if int(orch_fence) != row["fence"] or (holder is not None and holder != row["holder"]):
        raise StaleFence(None, int(orch_fence), int(row["fence"]))
    db.execute("UPDATE board_lease SET expires_ts = ? WHERE id = 1",
               (max(row["expires_ts"], now + DEFAULT_LEASE_S),))
    return str(row["holder"])


def propose_task(db: sqlite3.Connection, *, title: str, body: str, creator: str,
                 files: Iterable[str] = (), origin: str | None = None,
                 now: float | None = None) -> int:
    """Anyone proposes (I9). A proposal is never wakeable (I4). A repeated
    `origin` (a relay redelivery) returns the existing id."""
    paths = _declare(files)
    now = time.time() if now is None else float(now)
    with _tx(db):
        if origin is not None:
            row = db.execute("SELECT id FROM tasks WHERE origin = ?", (origin,)).fetchone()
            if row is not None:
                return int(row["id"])
        tid = _insert_task(db, title=title, body=body, state="proposed",
                           creator=creator, assignee=None, reviewer=None,
                           origin=origin, now=now)
        _insert_files(db, tid, paths)
        _event(db, "proposed", tid, creator, {"origin": origin, "files": paths}, now)
    return tid


def create_task(db: sqlite3.Connection, *, title: str, body: str, creator: str,
                assignee: str, reviewer: str | None = None,
                files: Iterable[str] = (), orch_fence: int,
                now: float | None = None) -> int:
    if not assignee:
        raise ValueError("assignee is required (propose_task is for unassigned work)")
    paths = _declare(files)
    now = time.time() if now is None else float(now)
    with _tx(db):
        _require_orch(db, orch_fence=orch_fence, now=now, holder=creator)
        tid = _insert_task(db, title=title, body=body, state="backlog",
                           creator=creator, assignee=assignee, reviewer=reviewer,
                           origin=None, now=now)
        _insert_files(db, tid, paths)
        _event(db, "created", tid, creator,
               {"assignee": assignee, "reviewer": reviewer, "files": paths,
                "orch_fence": int(orch_fence)}, now)
    return tid


def assign_task(db: sqlite3.Connection, *, task_id: int, assignee: str,
                reviewer: str | None = None, orch_fence: int,
                now: float | None = None) -> None:
    """proposed|backlog -> backlog. Accepting a proposal is assigning it."""
    if not assignee:
        raise ValueError("assignee is required")
    now = time.time() if now is None else float(now)
    with _tx(db):
        holder = _require_orch(db, orch_fence=orch_fence, now=now)
        row = _task(db, task_id)
        if row["state"] not in ("proposed", "backlog"):
            raise NotClaimable(int(task_id), row["state"])
        db.execute("UPDATE tasks SET state = 'backlog', assignee = ?, reviewer = ?,"
                   " updated_ts = ? WHERE id = ?", (assignee, reviewer, now, int(task_id)))
        _event(db, "assigned", int(task_id), holder,
               {"assignee": assignee, "reviewer": reviewer,
                "from_state": row["state"], "orch_fence": int(orch_fence)}, now)


def acquire_orchestration(db: sqlite3.Connection, *, holder: str,
                          ttl_s: float = DEFAULT_LEASE_S,
                          now: float | None = None) -> int:
    """Only with no holder or an expired lease (§6.4). Fence +1 every time."""
    now = time.time() if now is None else float(now)
    with _tx(db):
        row = _lease_row(db)
        if row is not None and row["expires_ts"] > now:
            raise OrchestrationHeld(str(row["holder"]), float(row["expires_ts"]))
        fence = (int(row["fence"]) if row is not None else 0) + 1
        db.execute(
            "INSERT INTO board_lease (id, holder, fence, acquired_ts, expires_ts)"
            " VALUES (1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET"
            " holder = excluded.holder, fence = excluded.fence,"
            " acquired_ts = excluded.acquired_ts, expires_ts = excluded.expires_ts",
            (holder, fence, now, now + float(ttl_s)))
        _event(db, "orchestration_acquired", None, holder,
               {"fence": fence, "ttl_s": float(ttl_s),
                "previous": row["holder"] if row is not None else None}, now)
    return fence


def renew_orchestration(db: sqlite3.Connection, *, holder: str, orch_fence: int,
                        ttl_s: float = DEFAULT_LEASE_S,
                        now: float | None = None) -> None:
    now = time.time() if now is None else float(now)
    with _tx(db):
        _require_orch(db, orch_fence=orch_fence, now=now, holder=holder)
        db.execute("UPDATE board_lease SET expires_ts = ? WHERE id = 1",
                   (now + float(ttl_s),))
        _event(db, "orchestration_renewed", None, holder,
               {"fence": int(orch_fence), "ttl_s": float(ttl_s)}, now)


def release_orchestration(db: sqlite3.Connection, *, holder: str,
                          orch_fence: int, now: float | None = None) -> None:
    """Expires the lease now. The row stays: it carries the fence counter."""
    now = time.time() if now is None else float(now)
    with _tx(db):
        _require_orch(db, orch_fence=orch_fence, now=now, holder=holder)
        db.execute("UPDATE board_lease SET expires_ts = ? WHERE id = 1", (now,))
        _event(db, "orchestration_released", None, holder,
               {"fence": int(orch_fence)}, now)


@dataclass(frozen=True)
class Claim:
    task_id: int
    fence: int
    lease_expires_ts: float
    files: tuple[str, ...]


def claim_task(db: sqlite3.Connection, *, task_id: int, claimer: str,
               lease_s: float = DEFAULT_LEASE_S, now: float | None = None) -> Claim:
    """§6.2: one UPDATE decides the winner; the file reservation rides the
    same transaction, so a conflict reverts the whole claim."""
    now = time.time() if now is None else float(now)
    task_id = int(task_id)
    with _tx(db):
        cur = db.execute(
            "UPDATE tasks SET state = 'working', fence = fence + 1,"
            " lease_expires_ts = ?, updated_ts = ?"
            " WHERE id = ? AND state = 'backlog' AND assignee = ?",
            (now + float(lease_s), now, task_id, claimer))
        if cur.rowcount != 1:
            row = _task(db, task_id)
            if row["state"] == "working" and row["assignee"] == claimer:
                raise ClaimLost(task_id)
            if row["state"] not in ("backlog", "working"):
                raise NotClaimable(task_id, row["state"])
            if row["assignee"] != claimer:
                raise NotAssignee(task_id, row["assignee"])
            raise ClaimLost(task_id)
        paths = [r["path"] for r in db.execute(
            "SELECT path FROM task_files WHERE task_id = ? ORDER BY path", (task_id,))]
        for p in paths:
            try:
                db.execute("UPDATE task_files SET reserved = 1"
                           " WHERE task_id = ? AND path = ?", (task_id, p))
            except sqlite3.IntegrityError:
                held = db.execute("SELECT task_id FROM task_files"
                                  " WHERE path = ? AND reserved = 1", (p,)).fetchone()
                raise FilesReserved(task_id, p, int(held["task_id"])) from None
        fence = int(_task(db, task_id)["fence"])
        _event(db, "claimed", task_id, claimer,
               {"fence": fence, "lease_s": float(lease_s), "files": paths}, now)
    return Claim(task_id, fence, now + float(lease_s), tuple(paths))


def _require_exec(db: sqlite3.Connection, *, task_id: int, fence: int,
                  claimer: str) -> sqlite3.Row:
    """Every execution write: current fence first (a late executor learns by
    StaleFence, §7.2-2), then the assignee, then the state."""
    row = _task(db, task_id)
    if int(fence) != row["fence"]:
        raise StaleFence(int(task_id), int(fence), int(row["fence"]))
    if row["assignee"] != claimer:
        raise NotAssignee(int(task_id), row["assignee"])
    if row["state"] != "working":
        raise NotClaimable(int(task_id), row["state"])
    return row


def renew_task(db: sqlite3.Connection, *, task_id: int, fence: int, claimer: str,
               lease_s: float = DEFAULT_LEASE_S, now: float | None = None) -> None:
    now = time.time() if now is None else float(now)
    with _tx(db):
        _require_exec(db, task_id=task_id, fence=fence, claimer=claimer)
        db.execute("UPDATE tasks SET lease_expires_ts = ?, updated_ts = ? WHERE id = ?",
                   (now + float(lease_s), now, int(task_id)))
        _event(db, "renewed", int(task_id), claimer,
               {"fence": int(fence), "lease_s": float(lease_s)}, now)


def _move(db: sqlite3.Connection, task_id: int, state: str, now: float, *,
          attempts_delta: int = 0, bump_fence: bool = False,
          resumable: str | None = None) -> None:
    """Every exit from working goes through here, so the reservation drops
    on every one of them (§6.5)."""
    db.execute(
        "UPDATE tasks SET state = ?, lease_expires_ts = NULL,"
        " attempts = attempts + ?, fence = fence + ?,"
        " resumable = COALESCE(?, resumable), updated_ts = ? WHERE id = ?",
        (state, attempts_delta, 1 if bump_fence else 0, resumable, now, int(task_id)))
    db.execute("UPDATE task_files SET reserved = 0 WHERE task_id = ?", (int(task_id),))


def submit_task(db: sqlite3.Connection, *, task_id: int, fence: int, claimer: str,
                now: float | None = None) -> None:
    now = time.time() if now is None else float(now)
    with _tx(db):
        row = _require_exec(db, task_id=task_id, fence=fence, claimer=claimer)
        to = "review" if row["reviewer"] else "done"
        _move(db, task_id, to, now)
        _event(db, "submitted", int(task_id), claimer, {"fence": int(fence), "to": to}, now)


def review_task(db: sqlite3.Connection, *, task_id: int, fence: int, reviewer: str,
                verdict: str, now: float | None = None) -> None:
    """approve -> done; reject -> backlog with the same assignee and attempts
    intact (rejection is not an execution failure, §6.3)."""
    if verdict not in ("approve", "reject"):
        raise ValueError(f"verdict must be approve|reject, got {verdict!r}")
    now = time.time() if now is None else float(now)
    with _tx(db):
        row = _task(db, task_id)
        if reviewer == row["assignee"]:
            raise SelfReview(int(task_id))
        if int(fence) != row["fence"]:
            raise StaleFence(int(task_id), int(fence), int(row["fence"]))
        if row["state"] != "review":
            raise NotClaimable(int(task_id), row["state"])
        if reviewer != row["reviewer"]:
            raise NotReviewer(int(task_id), row["reviewer"])
        to = "done" if verdict == "approve" else "backlog"
        db.execute("UPDATE tasks SET state = ?, updated_ts = ? WHERE id = ?",
                   (to, now, int(task_id)))
        _event(db, "reviewed", int(task_id), reviewer,
               {"fence": int(fence), "verdict": verdict, "to": to}, now)


def release_task(db: sqlite3.Connection, *, task_id: int, fence: int, claimer: str,
                 reason: str, now: float | None = None) -> None:
    """working -> backlog, attempts + 1; at MAX_ATTEMPTS -> blocked (§7.6)."""
    now = time.time() if now is None else float(now)
    with _tx(db):
        row = _require_exec(db, task_id=task_id, fence=fence, claimer=claimer)
        attempts = int(row["attempts"]) + 1
        to = "blocked" if attempts >= MAX_ATTEMPTS else "backlog"
        _move(db, task_id, to, now, attempts_delta=1)
        _event(db, "released", int(task_id), claimer,
               {"fence": int(fence), "reason": str(reason), "attempts": attempts}, now)
        if to == "blocked":
            _event(db, "blocked", int(task_id), None,
                   {"attempts": attempts, "reason": "attempts"}, now)


def block_task(db: sqlite3.Connection, *, task_id: int, actor: str, reason: str,
               fence: int | None = None, orch_fence: int | None = None,
               now: float | None = None) -> None:
    """The executor blocks its own working task (fence); the holder blocks any
    open task (orch_fence). Exactly one of the two."""
    if (fence is None) == (orch_fence is None):
        raise ValueError("block_task needs exactly one of fence (executor) "
                         "or orch_fence (holder)")
    now = time.time() if now is None else float(now)
    with _tx(db):
        if fence is not None:
            _require_exec(db, task_id=task_id, fence=fence, claimer=actor)
        elif orch_fence is not None:
            _require_orch(db, orch_fence=int(orch_fence), now=now, holder=actor)
            row = _task(db, task_id)
            if row["state"] in (*TERMINAL_STATES, "blocked"):
                raise NotClaimable(int(task_id), row["state"])
        _move(db, task_id, "blocked", now)
        _event(db, "blocked", int(task_id), actor,
               {"reason": str(reason),
                "by": "executor" if fence is not None else "orchestrator"}, now)


def cancel_task(db: sqlite3.Connection, *, task_id: int, actor: str, reason: str,
                orch_fence: int, now: float | None = None) -> None:
    now = time.time() if now is None else float(now)
    with _tx(db):
        _require_orch(db, orch_fence=orch_fence, now=now, holder=actor)
        row = _task(db, task_id)
        if row["state"] in TERMINAL_STATES:
            raise NotClaimable(int(task_id), row["state"])
        _move(db, task_id, "cancelled", now)
        _event(db, "cancelled", int(task_id), actor, {"reason": str(reason)}, now)


def show_task(db: sqlite3.Connection, task_id: int) -> dict:
    with _reading():
        out = dict(_task(db, task_id))
        out["files"] = [{"path": r["path"], "reserved": bool(r["reserved"])}
                        for r in db.execute("SELECT path, reserved FROM task_files"
                                            " WHERE task_id = ? ORDER BY path",
                                            (int(task_id),))]
    return out


def list_tasks(db: sqlite3.Connection, *, state: str | None = None,
               assignee: str | None = None) -> list[dict]:
    with _reading():
        rows = db.execute(
            "SELECT id, title, state, creator, assignee, reviewer, fence, attempts,"
            " lease_expires_ts, updated_ts FROM tasks"
            " WHERE (? IS NULL OR state = ?) AND (? IS NULL OR assignee = ?)"
            " ORDER BY id", (state, state, assignee, assignee)).fetchall()
    return [dict(r) for r in rows]


def board_status(db: sqlite3.Connection, *, now: float) -> dict:
    """holder, counts per state, refused wakes in 24 h, stalled reviews."""
    with _reading():
        lease = _lease_row(db)
        counts = {r["state"]: int(r["n"]) for r in db.execute(
            "SELECT state, COUNT(*) AS n FROM tasks GROUP BY state")}
        refused = {r["kind"]: int(r["n"]) for r in db.execute(
            "SELECT kind, COUNT(*) AS n FROM board_events WHERE ts > ? AND kind IN"
            " ('no_connector', 'budget_exhausted', 'admission_denied',"
            "  'liveness_unknown', 'agent_live', 'concurrency', 'files_reserved',"
            "  'wake_failed') GROUP BY kind", (now - 86400,))}
        stalled = [int(r["task_id"]) for r in db.execute(
            "SELECT DISTINCT e.task_id FROM board_events e JOIN tasks t"
            " ON t.id = e.task_id WHERE e.kind = 'review_stalled'"
            " AND t.state = 'review' ORDER BY e.task_id")]
    live = lease is not None and lease["expires_ts"] > now
    holder = str(lease["holder"]) if live and lease is not None else None
    orch_expires = _iso(float(lease["expires_ts"])) if live and lease is not None else None
    return {"schema": SCHEMA_VERSION,
            "holder": holder,
            "orch_expires": orch_expires,
            "counts": counts, "refused_wakes_24h": refused,
            "stalled_reviews": stalled}


def log_event(db: sqlite3.Connection, kind: str, task_id: int | None,
              actor: str | None, payload: dict, now: float) -> None:
    """A write whose only content IS the event (notice, gate, sweeper)."""
    with _tx(db):
        _event(db, kind, task_id, actor, payload, now)


def record_sample(db: sqlite3.Connection, *, ts: float, load1: float,
                  mem_available_mb: int) -> None:
    """Sweep step 1. Keeps the last SAMPLE_KEEP rows. Not an event (§B-6)."""
    with _tx(db):
        db.execute("INSERT OR REPLACE INTO admission_samples (ts, load1,"
                   " mem_available_mb) VALUES (?, ?, ?)",
                   (float(ts), float(load1), int(mem_available_mb)))
        db.execute("DELETE FROM admission_samples WHERE ts NOT IN (SELECT ts FROM"
                   " admission_samples ORDER BY ts DESC LIMIT ?)", (SAMPLE_KEEP,))


def recent_samples(db: sqlite3.Connection) -> list[tuple[float, float, int]]:
    with _reading():
        return [(float(r["ts"]), float(r["load1"]), int(r["mem_available_mb"]))
                for r in db.execute("SELECT ts, load1, mem_available_mb"
                                     " FROM admission_samples ORDER BY ts")]


def expire_leases(db: sqlite3.Connection, *, now: float) -> list[dict]:
    """Sweep step 2. The fence moves +1 (§B-1): expiry ENDS the execution
    epoch, so the late executor's next write fails with StaleFence."""
    out: list[dict] = []
    with _tx(db):
        rows = db.execute("SELECT id, attempts, fence FROM tasks WHERE state = 'working'"
                          " AND lease_expires_ts <= ? ORDER BY id", (now,)).fetchall()
        for row in rows:
            tid = int(row["id"])
            sess = db.execute("SELECT session_id, pid FROM sessions WHERE task_id = ?"
                              " AND state = 'running' ORDER BY started_ts DESC LIMIT 1",
                              (tid,)).fetchone()
            attempts = int(row["attempts"]) + 1
            to = "blocked" if attempts >= MAX_ATTEMPTS else "backlog"
            _move(db, tid, to, now, attempts_delta=1, bump_fence=True,
                  resumable=str(sess["session_id"]) if sess else None)
            payload: dict = {"attempts": attempts, "fence": int(row["fence"]) + 1}
            if sess is not None:
                payload["session"] = str(sess["session_id"])
                payload["pid"] = sess["pid"]
            _event(db, "lease_expired", tid, None, payload, now)
            if to == "blocked":
                _event(db, "blocked", tid, None, {"attempts": attempts, "reason": "attempts"}, now)
            out.append({"task_id": tid, "to": to, **payload})
    return out


def orchestration_expired(db: sqlite3.Connection, *, now: float) -> bool:
    """Sweep step 3: one event per expired fence, none after a release."""
    with _tx(db):
        row = _lease_row(db)
        if row is None or row["expires_ts"] > now:
            return False
        said = db.execute(
            "SELECT 1 FROM board_events WHERE kind IN ('orchestration_expired',"
            " 'orchestration_released') AND json_extract(payload, '$.fence') = ?",
            (int(row["fence"]),)).fetchone()
        if said is not None:
            return False
        _event(db, "orchestration_expired", None, str(row["holder"]),
               {"fence": int(row["fence"])}, now)
    return True


def event_counts(db: sqlite3.Connection, *, since_ts: float) -> dict[str, int]:
    with _reading():
        return {r["kind"]: int(r["n"]) for r in db.execute(
            "SELECT kind, COUNT(*) AS n FROM board_events WHERE ts >= ?"
            " GROUP BY kind ORDER BY kind", (since_ts,))}


def prune(db: sqlite3.Connection, *, now: float,
          max_age_days: float = RETENTION_DAYS) -> dict:
    """doctor --prune only, never the sweep (§6.6). 90 days is past the
    21-day kill-criteria window, so the window is never touched."""
    cutoff = now - float(max_age_days) * 86400
    with _tx(db):
        old = [int(r["id"]) for r in db.execute(
            "SELECT id FROM tasks WHERE state IN ('done', 'cancelled')"
            " AND updated_ts < ?", (cutoff,))]
        db.executemany("DELETE FROM task_files WHERE task_id = ?", [(i,) for i in old])
        db.executemany("DELETE FROM tasks WHERE id = ?", [(i,) for i in old])
        events = db.execute("DELETE FROM board_events WHERE ts < ?", (cutoff,)).rowcount
        _event(db, "pruned", None, None, {"tasks": len(old), "events": events,
                                          "cutoff": cutoff}, now)
    return {"tasks": len(old), "events": events}


def pending_executor(db: sqlite3.Connection) -> list[sqlite3.Row]:
    with _reading():
        return db.execute("SELECT * FROM tasks WHERE state = 'backlog'"
                          " AND assignee IS NOT NULL ORDER BY id").fetchall()


def pending_review(db: sqlite3.Connection) -> list[sqlite3.Row]:
    with _reading():
        return db.execute("SELECT * FROM tasks WHERE state = 'review'"
                          " AND reviewer IS NOT NULL ORDER BY id").fetchall()


def events_for(db: sqlite3.Connection, kind: str, task_id: int, fence: int, *,
               role: str | None = None) -> list[sqlite3.Row]:
    """Events of one (task, fence[, role]). The notice key carries the role:
    a rejected task goes back to backlog on the fence its reviewer was
    notified on, and the executor must still be told."""
    with _reading():
        return db.execute(
            "SELECT ts, payload FROM board_events WHERE kind = ? AND task_id = ?"
            " AND json_extract(payload, '$.fence') = ?"
            " AND (? IS NULL OR json_extract(payload, '$.role') = ?) ORDER BY id",
            (kind, int(task_id), int(fence), role, role)).fetchall()


def session_started(db: sqlite3.Connection, *, session_id: str, instance_id: str,
                    task_id: int, connector: str, pid: int | None, fence: int,
                    now: float) -> None:
    with _tx(db):
        db.execute("INSERT OR REPLACE INTO sessions (session_id, instance_id, task_id,"
                   " connector, pid, started_ts, state) VALUES (?, ?, ?, ?, ?, ?, 'running')",
                   (session_id, instance_id, int(task_id), connector, pid, now))
        _event(db, "wake_started", int(task_id), instance_id,
               {"fence": int(fence), "session": session_id, "pid": pid,
                "connector": connector}, now)


def end_session(db: sqlite3.Connection, *, session_id: str, state: str, now: float) -> None:
    with _tx(db):
        row = db.execute("SELECT task_id FROM sessions WHERE session_id = ?"
                         " AND state = 'running'", (session_id,)).fetchone()
        if row is None:
            return
        db.execute("UPDATE sessions SET state = ? WHERE session_id = ?", (state, session_id))
        _event(db, "session_ended", row["task_id"], None,
               {"session": session_id, "state": state}, now)


def session_row(db: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    with _reading():
        return db.execute("SELECT * FROM sessions WHERE session_id = ?",
                          (session_id,)).fetchone()


def running_sessions(db: sqlite3.Connection, *,
                     instance_id: str | None = None) -> list[sqlite3.Row]:
    with _reading():
        return db.execute("SELECT * FROM sessions WHERE state = 'running'"
                          " AND (? IS NULL OR instance_id = ?) ORDER BY started_ts",
                          (instance_id, instance_id)).fetchall()


def wakes_started(db: sqlite3.Connection, *, instance_id: str, since_ts: float) -> int:
    """The budget reads board_events, no new ledger (R4)."""
    with _reading():
        return int(db.execute("SELECT COUNT(*) FROM board_events WHERE kind = 'wake_started'"
                              " AND actor = ? AND ts >= ?",
                              (instance_id, since_ts)).fetchone()[0])


def last_gate(db: sqlite3.Connection, task_id: int, fence: int) -> tuple[str, str] | None:
    with _reading():
        row = db.execute(
            "SELECT kind, payload FROM board_events WHERE task_id = ?"
            " AND json_extract(payload, '$.fence') = ? AND kind IN ('no_connector',"
            " 'budget_exhausted', 'admission_denied', 'liveness_unknown', 'agent_live',"
            " 'concurrency', 'files_reserved') ORDER BY id DESC LIMIT 1",
            (int(task_id), int(fence))).fetchone()
    if row is None:
        return None
    return str(row["kind"]), str(json.loads(row["payload"]).get("reason", ""))


