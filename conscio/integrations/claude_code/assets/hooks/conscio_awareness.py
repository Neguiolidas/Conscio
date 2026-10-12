#!/usr/bin/env python3
"""Conscio SessionStart awareness (R3 — DEFENSIVE).

Prints ONE line so the agent knows Conscio is available, and — when it applies
— a second one saying which capability looks lost and how to restore it. Runs
on every session, so it must never delay or break one: everything is wrapped,
the work is trivial, and it ALWAYS exits 0 — even if something unexpected
throws.

Zero imports from the `conscio` package: hooks run under a plain `python3`
with nothing installed beside them, so the marker is read by hand here, the
same way the honesty hook re-implements blob reading.
"""
import argparse
import json
import os
import shlex
import sys
from pathlib import Path

#: Args that older installs used to carry a capability. They are never emitted
#: again — the capability lives in the space now — but their presence in a
#: previous version's cache is the only surviving record that consent existed.
CAPABILITY_ARGS = {"--enable-relay": "relay", "--can-create-halls": "halls"}

#: Legacy hook files a pre-4.9.2 materialized install may still have under
#: the claude dir's hooks/. The installer's legacy_claude keeps the same
#: literal list; a contract test asserts the two stay equal.
LEGACY_HOOK_FILES = [
    "conscio_awareness.py",
    "conscio_deepminer.py",
    "conscio_deepminer.json",
    "conscio_obsstore.py",
    "conscio_wake.py",
    "conscio_honesty.py",
    "conscio_honesty_pkg/__init__.py",
]

_MARKER = "mcp_capabilities"


def _claude_dir() -> Path:
    """CLAUDE_DIR env else ~/.claude — exactly like v4.9.1 materialize.py."""
    override = os.environ.get("CLAUDE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude"


def legacy_notice():
    """One line when an old materialized install left hooks behind, else None.

    Stat only: no .claude.json parse, no package import. Total by contract
    on Python 3.10-3.13 AND 3.14: on 3.10-3.13 ``Path.exists``/``is_dir``
    propagate a ``PermissionError`` (an OSError) for a directory the user
    cannot read, while 3.14 returns False — so the stats are wrapped in
    ``except OSError`` and the notice returns None, never raising. The call
    in main() is deliberately NOT inside its broad try: a raise here is a
    real defect (the M6 mutation shape), and the __main__ guard below still
    keeps the hook at exit 0 in real sessions.
    """
    try:
        claude = _claude_dir()
        hooks = claude / "hooks"
        if not hooks.is_dir():
            return None
        if not any((hooks / rel).exists() for rel in LEGACY_HOOK_FILES):
            return None
    except OSError:
        return None
    return ("Conscio: legacy hook files from a pre-4.9.2 install are still "
            f"under {claude}/hooks; retire them with: "
            "conscio init --host claude-code --remove-legacy")


def _line() -> str:
    return ("Conscio is available: use the conscio.* MCP tools (recall, remember, "
            "state, handoff) and the /conscio:* commands. Recall before non-trivial "
            "work; remember settled decisions. /conscio:mode changes the tool surface.")


def _granted(space) -> set:
    try:
        raw = Path(space).expanduser().joinpath(_MARKER).read_text("utf-8")
    except (OSError, TypeError, ValueError):
        return set()
    return {ln.strip() for ln in raw.splitlines() if ln.strip()}


def consent_warning(space, plugin_root):
    """One line naming a capability that looks lost, or None.

    A plugin update recreates the cached `.mcp.json` from the asset, and the
    asset never carries a capability argument — so an install that had one
    loses it with no signal. That is A3, and 4.6.4 changed its owner without
    removing it.

    This does NOT restore anything. Consent is born from a present action of
    the user, never from a retroactive artefact: a previous version's cache is
    the past, and with A3 in play the absence of the flag is ambiguous between
    the user revoking and the bug erasing. So it speaks and lets the user
    decide — the cure for a silent failure is to make it speak, not to guess.

    Silence is also the answer for a fresh install: announcing a loss that
    never happened would train the reader to ignore the line.
    """
    try:
        atual = Path(plugin_root).expanduser().resolve()
        perdidas = set()
        for irmao in atual.parent.iterdir():
            if not irmao.is_dir() or irmao == atual:
                continue
            try:
                entrada = json.loads(
                    (irmao / ".mcp.json").read_text("utf-8"))
                args = entrada["mcpServers"]["conscio"]["args"]
            except Exception:
                continue          # cache quebrado nao derruba a sessao
            perdidas |= {CAPABILITY_ARGS[a] for a in args
                         if a in CAPABILITY_ARGS}
        perdidas -= _granted(space)
        if not perdidas:
            return None
        nomes = ", ".join(sorted(perdidas))
        cmds = "; ".join(
            f"conscio capabilities enable {n} --storage {shlex.quote(str(space))}"
            for n in sorted(perdidas))
        return (f"Conscio: a previous install had {nomes} enabled and this space "
                f"has no record of it. A plugin update moves capabilities into "
                f"the space, and this consent did not travel. NOTHING was granted "
                f"automatically — to restore it: {cmds}")
    except Exception:
        return None


def main() -> None:
    try:
        sys.stdout.write(_line() + "\n")
    except Exception:
        pass
    # The notice is stat-only and total by contract, so it is NOT swallowed:
    # a raise here is a real defect (the M6 mutation shape). The __main__
    # guard below still keeps the hook at exit 0 in real sessions.
    notice = legacy_notice()
    if notice:
        sys.stdout.write(notice + "\n")
    try:
        ap = argparse.ArgumentParser(add_help=False)
        ap.add_argument("--storage", default="")
        args, _ = ap.parse_known_args()
        if args.storage:
            aviso = consent_warning(args.storage, Path(__file__).resolve().parent.parent)
            if aviso:
                sys.stdout.write(aviso + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
