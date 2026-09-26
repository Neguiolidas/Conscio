# conscio/ambient/surface.py
"""One path for the CLI and the MCP tool (§8). Three copies of "how do I
write the board" is how one of them drifts (the lesson of _deliver_to_peer).

The actor is always the caller's identity, never an argument: there is no
`as=`. The holder's orch_fence lives in the holder's space (atomic write), so
an agent never carries the number between turns.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from . import board, paths

ORCH_FILE = "ambient_orch.json"
OPS = ("show", "list", "status", "propose", "create", "assign", "claim", "renew",
       "submit", "review", "release", "block", "cancel", "orchestrate")


def _orch_path(space: Path) -> Path:
    return Path(space).expanduser() / ORCH_FILE


def load_orch_fence(space: Path, board_file: Path) -> int:
    """0 when this space holds no fence for THIS board. Fences start at 1,
    so 0 never passes the dispatch check."""
    try:
        data = json.loads(_orch_path(space).read_text("utf-8"))
        if isinstance(data, dict) and data.get("board") == str(board_file):
            return int(data.get("fence", 0))
    except (OSError, ValueError, TypeError):
        pass
    return 0


def _save_orch_fence(space: Path, board_file: Path, fence: int) -> None:
    path = _orch_path(space)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({"board": str(board_file), "fence": int(fence)},
                      sort_keys=True).encode("utf-8")
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        written = 0
        while written < len(blob):
            written += os.write(fd, blob[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def run_op(args: dict, *, actor: str, space: Path, now: float | None = None) -> dict:
    op = str(args.get("op", ""))
    if op not in OPS:
        return {"ok": False, "error": f"unknown op {op!r}; expected one of {'|'.join(OPS)}"}
    if not actor:
        return {"ok": False, "error": "no identity: the board needs this agent's "
                                      "instance id (CONSCIO_SELF_ID)"}
    when = time.time() if now is None else float(now)
    board_file = paths.board_path()
    try:
        db = board.open_board(board_file)
    except board.BoardError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        return {"ok": True, **_dispatch(db, op, args, actor=actor, space=Path(space),
                                        board_file=board_file, now=when)}
    except board.BoardError as exc:
        return {"ok": False, "error": str(exc)}
    except (KeyError, TypeError, ValueError) as exc:
        return {"ok": False, "error": f"invalid arguments for {op}: {exc}"}
    finally:
        db.close()


def _dispatch(db: sqlite3.Connection, op: str, a: dict, *, actor: str, space: Path,
              board_file: Path, now: float) -> dict:
    def orch() -> int:
        return load_orch_fence(space, board_file)

    def tid() -> int:
        return int(a["task_id"])

    lease_s = float(a.get("lease_s", board.DEFAULT_LEASE_S))
    if op == "show":
        return {"task": board.show_task(db, tid())}
    if op == "list":
        return {"tasks": board.list_tasks(db, state=a.get("state"),
                                          assignee=a.get("assignee"))}
    if op == "status":
        return {"status": board.board_status(db, now=now)}
    if op == "propose":
        return {"task_id": board.propose_task(db, title=a["title"], body=a.get("body", ""),
                                              creator=actor, files=a.get("files") or ())}
    if op == "create":
        return {"task_id": board.create_task(
            db, title=a["title"], body=a.get("body", ""), creator=actor,
            assignee=a["assignee"], reviewer=a.get("reviewer"),
            files=a.get("files") or (), orch_fence=orch())}
    if op == "assign":
        board.assign_task(db, task_id=tid(), assignee=a["assignee"],
                          reviewer=a.get("reviewer"), orch_fence=orch())
        return {"task_id": tid()}
    if op == "claim":
        c = board.claim_task(db, task_id=tid(), claimer=actor, lease_s=lease_s, now=now)
        return {"task_id": c.task_id, "fence": c.fence,
                "lease_expires": board._iso(c.lease_expires_ts), "files": list(c.files)}
    if op == "renew":
        board.renew_task(db, task_id=tid(), fence=int(a["fence"]), claimer=actor,
                         lease_s=lease_s, now=now)
        return {"task_id": tid()}
    if op == "submit":
        board.submit_task(db, task_id=tid(), fence=int(a["fence"]), claimer=actor)
        return {"task_id": tid()}
    if op == "review":
        board.review_task(db, task_id=tid(), fence=int(a["fence"]), reviewer=actor,
                          verdict=str(a["verdict"]))
        return {"task_id": tid()}
    if op == "release":
        board.release_task(db, task_id=tid(), fence=int(a["fence"]), claimer=actor,
                           reason=str(a.get("reason", "")))
        return {"task_id": tid()}
    if op == "block":
        if a.get("fence") is not None:
            board.block_task(db, task_id=tid(), actor=actor, reason=str(a["reason"]),
                             fence=int(a["fence"]))
        else:
            board.block_task(db, task_id=tid(), actor=actor, reason=str(a["reason"]),
                             orch_fence=orch())
        return {"task_id": tid()}
    if op == "cancel":
        board.cancel_task(db, task_id=tid(), actor=actor, reason=str(a["reason"]),
                          orch_fence=orch())
        return {"task_id": tid()}
    action = str(a["action"])                      # op == "orchestrate"
    ttl = float(a.get("ttl_s", board.DEFAULT_LEASE_S))
    if action == "acquire":
        fence = board.acquire_orchestration(db, holder=actor, ttl_s=ttl, now=now)
        _save_orch_fence(space, board_file, fence)
        return {"orch_fence": fence}
    if action == "renew":
        board.renew_orchestration(db, holder=actor, orch_fence=orch(), ttl_s=ttl, now=now)
        return {"orch_fence": orch()}
    if action == "release":
        board.release_orchestration(db, holder=actor, orch_fence=orch())
        _orch_path(space).unlink(missing_ok=True)
        return {"released": True}
    raise ValueError(f"unknown action {action!r}; expected acquire|renew|release")
