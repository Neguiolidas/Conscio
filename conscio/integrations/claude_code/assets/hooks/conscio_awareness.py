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
import shlex
import sys
from pathlib import Path

#: Args that older installs used to carry a capability. They are never emitted
#: again — the capability lives in the space now — but their presence in a
#: previous version's cache is the only surviving record that consent existed.
CAPABILITY_ARGS = {"--enable-relay": "relay", "--can-create-halls": "halls"}

_MARKER = "mcp_capabilities"


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
    main()
    sys.exit(0)
