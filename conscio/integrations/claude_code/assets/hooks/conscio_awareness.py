#!/usr/bin/env python3
"""Conscio SessionStart awareness (R3 — DEFENSIVE).

Prints ONE line so the agent knows Conscio is available. Runs on every session,
so it must never delay or break one: everything is wrapped, the work is trivial,
and it ALWAYS exits 0 — even if something unexpected throws."""
import sys


def _line() -> str:
    return ("Conscio is available natively: use the conscio.* MCP tools "
            "(recall/remember/state/society/relay) and /conscio:* commands. "
            "Recall relevant memory before non-trivial work; remember settled "
            "decisions.")


def main() -> None:
    try:
        sys.stdout.write(_line() + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    raise SystemExit(0)
