# conscio/ambient/cli.py
"""conscio ambient — the board from a terminal (§8). Same run_op as the MCP tool."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import surface


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
    return p


def _args_of(ns: argparse.Namespace) -> dict:
    if ns.cmd == "status":
        return {"op": "status"}
    if ns.cmd == "orchestrate":
        return {"op": "orchestrate", "action": ns.action, "ttl_s": ns.ttl_s}
    skip = {"cmd", "storage"}
    return {k: v for k, v in vars(ns).items() if k not in skip and v not in (None, [])}


def main(argv: list[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    try:
        space, actor = _space_and_actor(ns.storage)
    except Exception as exc:                       # AmbiguousSpace and friends
        print(str(exc), file=sys.stderr)
        return 2
    args = {k: v for k, v in _args_of(ns).items() if v is not None}
    out = surface.run_op(args, actor=actor, space=space)
    if not out.get("ok"):
        print(out.get("error", "unknown error"), file=sys.stderr)
        return 1
    out.pop("ok")
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0
