# conscio/ambient/node.py
"""Conscio Ambient node (v4.8 S3): the sweep that rides the reactor tick.

Engine-free, like the reactor it lives in. It NEVER raises into the relay
tick (test_node_exception_never_stops_relay_tick): a board problem costs one
sweep, never the message delivery the reactor exists for.
"""

from __future__ import annotations

import fcntl
import logging
import os
import statistics
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from . import board, paths

log = logging.getLogger("conscio.ambient.node")

# NÃO DETERMINADO until probe S3 (spec §7.3). Named so calibrating is one edit.
WAKE_FLOOR_MB = 1500
DELTA_TOLERADO = 1.5
ADMISSION_WINDOW = 36
ADMISSION_MAX_AGE_S = 360


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
                 clock: Callable[[], float] = time.time) -> None:
        self.self_id = self_id
        self.liaison_db = liaison_db
        self.root = root
        self.proc_root = Path(proc_root)
        self.clock = clock
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
