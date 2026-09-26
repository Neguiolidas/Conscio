# conscio/ambient/doctor.py
"""conscio ambient doctor: what the board cannot see by itself. Suggests,
never kills (the D5 pattern of the S1 doctor)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import board, node, paths


def wake_residue(db: sqlite3.Connection, *, proc_root: Path) -> list[dict]:
    """Processes still carrying CONSCIO_WAKE_TASK=<id> whose task left
    working. The fence protects the board, not the disk (H30-1)."""
    from ..liaison.relay_cli import _read_proc_environ
    out: list[dict] = []
    try:
        entries = sorted((e for e in Path(proc_root).iterdir() if e.name.isdigit()),
                         key=lambda e: int(e.name))
    except OSError:
        return out
    for entry in entries:
        raw = (_read_proc_environ(entry) or {}).get("CONSCIO_WAKE_TASK", "")
        if not raw.isdigit():
            continue
        row = db.execute("SELECT state FROM tasks WHERE id = ?", (int(raw),)).fetchone()
        state = str(row["state"]) if row is not None else "missing"
        if state != "working":
            out.append({"pid": int(entry.name), "task_id": int(raw), "state": state})
    return out


def run(*, root: Path | None = None, proc_root: Path = Path("/proc"),
        prune: bool = False, now: float) -> list[str]:
    lines = [f"flag: {'on' if paths.flag_path(root).is_file() else 'off'}"]
    path = paths.board_path(root)
    if not path.exists():
        return [*lines, f"board: absent ({path})"]
    try:
        db = board.open_board(path)
    except board.BoardError as exc:
        return [*lines, f"board: {exc}"]
    try:
        lines.append(f"board: {path} (schema v{board.SCHEMA_VERSION})")
        sweeper = db.execute("SELECT actor, ts FROM board_events WHERE kind = 'sweeper'"
                             " ORDER BY id DESC LIMIT 1").fetchone()
        lines.append(f"sweeper: {sweeper['actor']} since {board._iso(sweeper['ts'])}"
                     if sweeper else "sweeper: none yet")
        status = board.board_status(db, now=now)
        lines.append(f"orchestration: {status['holder']} until {status['orch_expires']}"
                     if status["holder"] else "orchestration: none (execute-only)")
        samples = board.recent_samples(db)
        ready = node.admission(samples, now=now, load1_now=0.0,
                               mem_available_mb_now=node.WAKE_FLOOR_MB) == ""
        lines.append(f"admission baseline: {'ready' if ready else 'not ready'}"
                     f" ({len(samples)}/{node.ADMISSION_WINDOW} samples)")
        registry, error = node.load_registry(root)
        if error:
            lines.append(f"registry: {error}")
        elif not registry:
            lines.append("registry: absent (nobody is woken)")
        else:
            lines.append("registry: " + ", ".join(
                f"{iid} ({e.get('connector')}, budget {e.get('wake_budget_per_day', 0)}/day)"
                for iid, e in sorted(registry.items())))
        for r in wake_residue(db, proc_root=proc_root):
            lines.append(f"AVISO: processo {r['pid']} carrega CONSCIO_WAKE_TASK={r['task_id']},"
                         f" mas a task está em {r['state']}. Sugestão: kill {r['pid']}")
        if prune:
            got = board.prune(db, now=now)
            lines.append(f"pruned: {got['tasks']} tasks, {got['events']} events"
                         f" (older than {board.RETENTION_DAYS} days)")
    finally:
        db.close()
    return lines
