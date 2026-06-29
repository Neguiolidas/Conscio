"""`conscio init` entry — builds a terminal PromptIO and runs the wizard.

The IO swallows EOFError (closed stdin in CI/non-interactive) and returns the
default, so an unattended run never hangs (Hermet plan-gate ressalva)."""
from __future__ import annotations

import argparse
import time

from . import wizard


class _TerminalIO:
    def ask(self, prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            ans = input(f"{prompt}{suffix}: ").strip()
        except EOFError:
            return default
        return ans or default

    def confirm(self, prompt: str, default: bool = False) -> bool:
        d = "Y/n" if default else "y/N"
        try:
            ans = input(f"{prompt} ({d}): ").strip().lower()
        except EOFError:
            return default
        if not ans:
            return default
        return ans in ("y", "yes")

    def echo(self, msg: str) -> None:
        print(msg)


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(prog="conscio init")
    p.add_argument("--host", choices=["claude-code", "antigravity", "other"],
                   default="other")
    p.add_argument("--label", default=None)
    p.add_argument("--repair", action="store_true",
                   help="revalidate/rewrite this host's binding only")
    p.add_argument("--model", default=None)
    args = p.parse_args(argv)
    ts = time.strftime("%Y%m%d-%H%M%S")
    return wizard.run_with(_TerminalIO(), host=args.host, repair=args.repair,
                           model=args.model, ts=ts)
