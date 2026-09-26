# conscio/ambient/node.py
"""Conscio Ambient node (v4.8 S3): the sweep that rides the reactor tick.

Engine-free, like the reactor it lives in. It NEVER raises into the relay
tick (test_node_exception_never_stops_relay_tick): a board problem costs one
sweep, never the message delivery the reactor exists for.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import statistics
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from ..liaison import directory, mailbox
from . import board, paths

log = logging.getLogger("conscio.ambient.node")

# NÃO DETERMINADO until probe S3 (spec §7.3). Named so calibrating is one edit.
WAKE_FLOOR_MB = 1500
DELTA_TOLERADO = 1.5
ADMISSION_WINDOW = 36
ADMISSION_MAX_AGE_S = 360

WAKE_GRACE_S = 600      # NÃO DETERMINADO (spec §7.4)
RENOTIFY_MAX = 3        # NÃO DETERMINADO (spec §7.4)


def relay_notice(self_id: str) -> Callable[[str, int], bool]:
    """I2: the notice carries the task id and nothing else. Whoever receives
    it rereads the board; the relay never carries task state."""
    def send(to: str, task_id: int) -> bool:
        from ..liaison import relay_transport
        card = directory.get(to)
        if card is None:
            return False
        return relay_transport.deliver(card, {
            "from": self_id, "to": to, "type": "task_dispatch",
            "payload": {"task_id": int(task_id)}})
    return send


def _proposal(payload: object) -> tuple[str, str, list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("board.propose payload must be an object")
    title, body = payload.get("title"), payload.get("body", "")
    files = payload.get("files", [])
    if not isinstance(title, str) or not title.strip():
        raise ValueError("board.propose needs a non-empty title")
    if not isinstance(body, str):
        raise ValueError("board.propose body must be a string")
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise ValueError("board.propose files must be a list of strings")
    return title, body, files


def read_loadavg(proc_root: Path) -> float:
    return float((Path(proc_root) / "loadavg").read_text("utf-8").split()[0])


def read_mem_available_mb(proc_root: Path) -> int:
    for line in (Path(proc_root) / "meminfo").read_text("utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise ValueError("MemAvailable missing from meminfo")


def admission(samples: Sequence[tuple[float, float, int]], *, now: float,
              load1_now: float, mem_available_mb_now: int) -> str:
    """'' when admitted, else the reason of admission_denied (§7.3). The
    baseline is the p50 of load1 over the last ADMISSION_WINDOW samples, and
    it is ready only when the oldest of them is at most ADMISSION_MAX_AGE_S
    old. No absolute load constant survives (owner decision 2)."""
    window = sorted(samples)[-ADMISSION_WINDOW:]
    if len(window) < ADMISSION_WINDOW or now - window[0][0] > ADMISSION_MAX_AGE_S:
        return "baseline_not_ready"
    if mem_available_mb_now < WAKE_FLOOR_MB:
        return "mem"
    if load1_now > statistics.median(s[1] for s in window) + DELTA_TOLERADO:
        return "load"
    return ""


def _try_flock(path: Path) -> int | None:
    """Non-blocking. The fd is non-inheritable (PEP 446), so a spawned agent
    never keeps the sweep lock alive after its reactor dies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


class Node:
    def __init__(self, *, self_id: str, liaison_db: Path | None = None,
                 root: Path | None = None, proc_root: Path = Path("/proc"),
                 clock: Callable[[], float] = time.time,
                 send: Callable[[str, int], bool] | None = None) -> None:
        self.self_id = self_id
        self.liaison_db = liaison_db
        self.root = root
        self.proc_root = Path(proc_root)
        self.clock = clock
        self.send = send or relay_notice(self_id)
        self._sweep_fd: int | None = None
        self._too_new_logged = False

    @property
    def board(self) -> Path:
        return paths.board_path(self.root)

    @property
    def is_sweeper(self) -> bool:
        return self._sweep_fd is not None

    def close(self) -> None:
        """Closing the fd is what gives the flock back (I12, §B-10)."""
        if self._sweep_fd is not None:
            try:
                os.close(self._sweep_fd)
            except OSError:
                pass
            self._sweep_fd = None

    def on_tick(self) -> None:
        try:
            self._on_tick()
        except Exception as exc:
            log.warning("ambient node skipped this tick: %s", exc)

    def _on_tick(self) -> None:
        if not paths.flag_path(self.root).is_file():
            self.close()                  # flag off: give the sweep back, touch nothing
            return
        try:
            db = board.open_board(self.board)
        except board.BoardTooNew as exc:
            if not self._too_new_logged:
                log.warning("ambient sweep skipped: %s", exc)
                self._too_new_logged = True
            self.close()
            return
        try:
            self._ingest_proposals(db)
            now = self.clock()
            if self._sweep_fd is None:
                self._sweep_fd = _try_flock(paths.sweep_lock_path(self.root))
                if self._sweep_fd is not None:
                    board.log_event(db, "sweeper", None, self.self_id,
                                    {"pid": os.getpid()}, now)
            if self._sweep_fd is not None:
                self.sweep(db, now=now)
        finally:
            db.close()

    def sweep(self, db, *, now: float) -> None:
        board.record_sample(db, ts=now, load1=read_loadavg(self.proc_root),
                            mem_available_mb=read_mem_available_mb(self.proc_root))
        board.expire_leases(db, now=now)
        board.orchestration_expired(db, now=now)
        self.deliver(db, now=now)

    def _ingest_proposals(self, db) -> int:
        """§9: board.propose from a directory peer becomes `proposed`, with
        creator = sender and origin = this mailbox row (a redelivery dedupes).
        Every flagged reactor reads ITS OWN mailbox; the sweep is not needed."""
        if self.liaison_db is None:
            return 0
        rows = mailbox.inbox(self.liaison_db, self.self_id, types=["board.propose"],
                             unread_only=True, limit=50)
        done: list[int] = []
        for r in rows:
            sender = str(r.get("from_instance") or "")
            try:
                if directory.get(sender) is None:
                    raise ValueError(f"board.propose from {sender!r}, not a directory peer")
                title, body, files = _proposal(r.get("payload"))
                board.propose_task(db, title=title, body=body, creator=sender, files=files,
                                   origin=f"relay:{self.self_id}:{int(r['id'])}")
            except board.BoardBusy:
                continue                                   # stays unread; next tick
            except (board.BoardError, ValueError, TypeError) as exc:
                mailbox.quarantine(self.liaison_db, source_row=int(r["id"]),
                                   motivo=f"board.propose refused: {exc}",
                                   payload_raw=json.dumps(r.get("payload"))[:4096])
            done.append(int(r["id"]))
        if done:
            mailbox.mark_read(self.liaison_db, done)
        return len(done)

    def _notify(self, db, tid: int, fence: int, to: str, role: str, now: float) -> bool:
        if not self.send(to, tid):
            return False                                   # no event: next sweep retries
        board.log_event(db, "notified", tid, self.self_id,
                        {"fence": fence, "to": to, "role": role}, now)
        return True

    def deliver(self, db, *, now: float) -> None:
        """Sweep step 4 (§7.4): the relay notice first, always."""
        for task in board.pending_executor(db):
            tid, fence = int(task["id"]), int(task["fence"])
            if not board.events_for(db, "notified", tid, fence, role="executor"):
                self._notify(db, tid, fence, str(task["assignee"]), "executor", now)
        self._renotify_reviewers(db, now=now)

    def _renotify_reviewers(self, db, *, now: float) -> None:
        """No review claim covers a session, so the reviewer only gets notices:
        repeated every WAKE_GRACE_S, at most RENOTIFY_MAX times, then
        review_stalled once (H30-2)."""
        for task in board.pending_review(db):
            tid, fence, to = int(task["id"]), int(task["fence"]), str(task["reviewer"])
            first = board.events_for(db, "notified", tid, fence, role="reviewer")
            if not first:
                self._notify(db, tid, fence, to, "reviewer", now)
                continue
            if board.events_for(db, "review_stalled", tid, fence):
                continue
            again = board.events_for(db, "renotified", tid, fence)
            if now - float((again or first)[-1]["ts"]) < WAKE_GRACE_S:
                continue
            if len(again) >= RENOTIFY_MAX:
                board.log_event(db, "review_stalled", tid, self.self_id,
                                {"fence": fence, "reviewer": to}, now)
                continue
            if self.send(to, tid):
                board.log_event(db, "renotified", tid, self.self_id,
                                {"fence": fence, "to": to, "n": len(again) + 1}, now)
