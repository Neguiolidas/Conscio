"""`conscio init` entry — builds a terminal PromptIO and runs the wizard.

The IO swallows EOFError (closed stdin in CI/non-interactive) and returns the
default, so an unattended run never hangs."""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import wizard


class _TerminalIO:
    # OSError included: a captured/closed stdin can raise it instead of
    # EOFError (e.g. under pytest) — unattended runs must never crash (R7)
    def ask(self, prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            ans = input(f"{prompt}{suffix}: ").strip()
        except (EOFError, OSError):
            return default
        return ans or default

    def confirm(self, prompt: str, default: bool = False) -> bool:
        d = "Y/n" if default else "y/N"
        try:
            ans = input(f"{prompt} ({d}): ").strip().lower()
        except (EOFError, OSError):
            return default
        if not ans:
            return default
        return ans in ("y", "yes")

    def echo(self, msg: str) -> None:
        print(msg)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="conscio init")
    p.add_argument("--host",
                   choices=["claude-code", "verdent", "antigravity", "other"],
                   default="other")
    p.add_argument("--label", default=None)
    p.add_argument("--repair", action="store_true",
                   help="revalidate/rewrite this host's binding only")
    p.add_argument("--model", default=None)
    p.add_argument("--remove-legacy", action="store_true",
                   help="move the old materialized claude-code install (hooks, "
                        "commands, skills, settings.json groups, MCP entry) "
                        "into a timestamped backup under the conscio base dir; "
                        "valid only with --host claude-code")
    p.add_argument("--retire-previous-space", action="store_true",
                   help="with --remove-legacy: proceed when the plugin resolves "
                        "to a different live identity; the legacy space stays on "
                        "disk, untouched, no longer run")
    args = p.parse_args(argv)
    if args.remove_legacy:
        if args.host != "claude-code":
            print("--remove-legacy is only valid with --host claude-code",
                  file=sys.stderr)
            return 2
        from . import legacy_claude
        return legacy_claude.remove_legacy(
            env=dict(os.environ),
            retire_previous_space=args.retire_previous_space,
            io=_TerminalIO())
    ts = time.strftime("%Y%m%d-%H%M%S")
    return wizard.run_with(_TerminalIO(), host=args.host, repair=args.repair,
                           model=args.model, ts=ts, label=args.label)
