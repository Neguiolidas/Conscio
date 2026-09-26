# conscio/ambient/cli.py
"""conscio ambient — the board from a terminal (§8). Same run_op as the MCP tool."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import board, surface


def _space_and_actor(storage: str) -> tuple[Path, str]:
    from ..space import resolve_live_space
    self_id = os.environ.get("CONSCIO_SELF_ID", "").strip()
    space = resolve_live_space(storage or None, self_id).path
    if not self_id:
        try:
            data = json.loads((space / "instance.json").read_text("utf-8"))
            self_id = str(data.get("instance_id", "")) if isinstance(data, dict) else ""
        except (OSError, ValueError):
            self_id = ""
    return space, self_id


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="conscio ambient",
                                description="Conscio Ambient: this machine's task board.")
    p.add_argument("--storage", default="", help="space to act as (default: the live space)")
    sub = p.add_subparsers(dest="cmd", required=True)
    task = sub.add_parser("task", help="read or write tasks")
    ops = task.add_subparsers(dest="op", required=True)

    def op(name: str, *, with_id: bool = True) -> argparse.ArgumentParser:
        sp = ops.add_parser(name)
        if with_id:
            sp.add_argument("task_id", type=int)
        return sp

    for name in ("propose", "create"):
        sp = op(name, with_id=False)
        sp.add_argument("--title", required=True)
        sp.add_argument("--body", default="")
        sp.add_argument("--file", dest="files", action="append", default=[])
        if name == "propose":
            sp.add_argument("--to")
        if name == "create":
            sp.add_argument("--assignee", required=True)
            sp.add_argument("--reviewer")
    sp = op("assign")
    sp.add_argument("--assignee", required=True)
    sp.add_argument("--reviewer")
    sp = op("list", with_id=False)
    sp.add_argument("--state")
    sp.add_argument("--assignee")
    op("show")
    sp = op("claim")
    sp.add_argument("--lease-s", dest="lease_s", type=float)
    sp = op("renew")
    sp.add_argument("--fence", type=int, required=True)
    sp.add_argument("--lease-s", dest="lease_s", type=float)
    sp = op("submit")
    sp.add_argument("--fence", type=int, required=True)
    sp = op("review")
    sp.add_argument("--fence", type=int, required=True)
    sp.add_argument("--verdict", choices=("approve", "reject"), required=True)
    sp = op("release")
    sp.add_argument("--fence", type=int, required=True)
    sp.add_argument("--reason", default="")
    sp = op("block")
    sp.add_argument("--fence", type=int)
    sp.add_argument("--reason", required=True)
    sp = op("cancel")
    sp.add_argument("--reason", required=True)
    orch = sub.add_parser("orchestrate", help="take, renew or give back the dispatch lease")
    orch.add_argument("action", choices=("acquire", "renew", "release"))
    orch.add_argument("--ttl-s", dest="ttl_s", type=float)
    sub.add_parser("status", help="holder, counts, refused wakes, stalled reviews")
    sub.add_parser("enable", help="turn the ambient node on for this machine")
    sub.add_parser("disable", help="turn it off (board and CLI keep working)")
    rep = sub.add_parser("report", help="board events counted by kind")
    rep.add_argument("--since", default="8h", help="window: 30m, 8h, 2d")
    doc = sub.add_parser("doctor", help="flag, board, sweeper, admission, wake residue")
    doc.add_argument("--prune", action="store_true",
                     help=f"delete events and done/cancelled tasks older than "
                          f"{board.RETENTION_DAYS} days")
    wk = sub.add_parser("wake", help="run the whole wake gate for one task, never spawn")
    wk.add_argument("task_id", type=int)
    wk.add_argument("--dry-run", action="store_true", required=True,
                    help="required: in v4.8 only the node wakes, never the CLI")
    return p


def _args_of(ns: argparse.Namespace) -> dict:
    if ns.cmd == "status":
        return {"op": "status"}
    if ns.cmd == "orchestrate":
        return {"op": "orchestrate", "action": ns.action, "ttl_s": ns.ttl_s}
    skip = {"cmd", "storage"}
    return {k: v for k, v in vars(ns).items() if k not in skip and v not in (None, [])}


_UNITS = {"m": 60, "h": 3600, "d": 86400}


def _since_s(text: str) -> float:
    text = text.strip()
    if len(text) < 2 or text[-1] not in _UNITS or not text[:-1].isdigit():
        raise ValueError(f"--since must look like 30m, 8h or 2d, got {text!r}")
    return int(text[:-1]) * _UNITS[text[-1]]


def _machine_cmd(ns: argparse.Namespace) -> int:
    import time

    from . import board, doctor, paths
    now = time.time()
    if ns.cmd == "enable":
        flag = paths.flag_path()
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(f"enabled {now:.0f}\n", encoding="utf-8")
        print(f"ambient: on ({flag})")
        return 0
    if ns.cmd == "disable":
        paths.flag_path().unlink(missing_ok=True)
        print("ambient: off (board and CLI keep working; nobody is woken)")
        return 0
    if ns.cmd == "doctor":
        print("\n".join(doctor.run(prune=ns.prune, now=now)))
        return 0
    try:
        window = _since_s(ns.since)
        db = board.open_board(paths.board_path())
    except (ValueError, board.BoardError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        counts = board.event_counts(db, since_ts=now - window)
    finally:
        db.close()
    print(json.dumps({"since": board._iso(now - window), "events": counts}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    if ns.cmd in ("enable", "disable", "report", "doctor"):
        return _machine_cmd(ns)
    try:
        space, actor = _space_and_actor(ns.storage)
    except Exception as exc:                       # AmbiguousSpace and friends
        print(str(exc), file=sys.stderr)
        return 2
    if ns.cmd == "wake":
        import time

        from . import board, node, paths
        db = board.open_board(paths.board_path())
        try:
            out = node.Node(self_id=actor).dry_run(db, ns.task_id, now=time.time())
        except board.BoardError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        finally:
            db.close()
        print(json.dumps(out, indent=2))
        return 0
    args = {k: v for k, v in _args_of(ns).items() if v is not None}
    out = surface.run_op(args, actor=actor, space=space)
    if not out.get("ok"):
        print(out.get("error", "unknown error"), file=sys.stderr)
        return 1
    out.pop("ok")
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0
