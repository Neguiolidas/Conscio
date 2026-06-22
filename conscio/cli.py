# conscio/cli.py
"""The `conscio` command — a thin, offline-safe surface over the shipped API.

Subcommands: version | info | reflect | plugins | bench. `bench` delegates
verbatim to `conscio.bench` (no logic duplication). `info`/`reflect` build a
ConsciousnessEngine offline (no LLM, no network) and default to an ephemeral
storage dir so a quick CLI look never clobbers a real workspace.

NOTE: as of v1.5.1, CLI commands default to the persistent storage dir
(~/.hermes/consciousness) so that awake/sleep state survives across calls.

Engine construction is deferred into the handlers, so `conscio version`,
`conscio --help`, and `conscio plugins` never build an engine.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__

DEFAULT_MODEL = "glm-5.1"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conscio",
        description="Conscio — self-awareness framework for AI agents.")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("version", help="print the Conscio version")

    p_info = sub.add_parser("info", help="show model context window / mode / budget")
    p_info.add_argument("model", nargs="?", default=DEFAULT_MODEL)
    p_info.add_argument("--storage", default="", help="storage dir (default: temp)")

    p_reflect = sub.add_parser("reflect", help="run one offline reflection cycle")
    p_reflect.add_argument("world_state", help="the world-state string to reflect on")
    p_reflect.add_argument("--model", default=DEFAULT_MODEL)
    p_reflect.add_argument("--confidence", type=float, default=0.8)
    p_reflect.add_argument("--storage", default="", help="storage dir (default: temp)")

    sub.add_parser("plugins", help="list discovered adapter/sensor/tool plugins")

    p_consent = sub.add_parser(
        "consent",
        help="grant/show structural-graph consent for the current workspace")
    p_consent.add_argument("scope", nargs="?",
                           choices=["off", "project", "parent"],
                           help="grant this scope (omit to show the current one)")
    p_consent.add_argument("--storage", default="", help="storage dir (default: ~/.hermes)")

    p_structure = sub.add_parser(
        "structure",
        help="show structural drift + freshness for the current workspace (read-only)")
    p_structure.add_argument(
        "--storage", default="", help="storage dir (default: ~/.hermes)")

    p_awake = sub.add_parser("awake",
                             help="enter Awake Mode (R9; enables autonomous run)")
    p_awake.add_argument("--model", default=DEFAULT_MODEL)
    p_awake.add_argument("--storage", default="", help="storage dir (default: temp)")

    p_sleep = sub.add_parser("sleep",
                             help="leave Awake Mode (R9; back to reflect-only)")
    p_sleep.add_argument("--model", default=DEFAULT_MODEL)
    p_sleep.add_argument("--storage", default="", help="storage dir (default: temp)")

    # Listed for discoverability; routed to conscio.{bench,daemon} before argparse.
    sub.add_parser("bench", add_help=False,
                   help="measure an inference backend (see: conscio bench --help)")
    sub.add_parser("daemon", add_help=False,
                   help="run the live heartbeat (see: conscio-daemon --help)")
    sub.add_parser("noosphere", add_help=False,
                   help="share skills across same-host instances "
                        "(see: conscio noosphere --help)")
    return parser


def _storage(arg: str) -> str:
    if arg:
        return arg
    # Persistent default so awake/sleep state survives across CLI calls. Route
    # through HERMES_HOME (default ~/.hermes) to match session_lifecycle/session_rag.
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    return str(home / "consciousness")


def _note_if_unknown(model: str, model_info) -> None:
    """Make a heuristic fallback visible — a typo'd model otherwise silently
    gets a default context window with no signal."""
    from .models import ModelRegistry
    if ModelRegistry.lookup(model) is None:
        ctx_k = model_info.context_window // 1000
        print(f"note: '{model}' is not a known model — using a heuristic "
              f"context window ({ctx_k}k, {model_info.mode.value}). "
              f"Register it with ModelRegistry.register(name, context_window=...) "
              f"or pass a known model.", file=sys.stderr)


def _cmd_version() -> int:
    print(__version__)
    return 0


def _cmd_info(model: str, storage: str) -> int:
    from .engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name=model, storage_path=_storage(storage))
    try:
        _note_if_unknown(model, eng.model_info)
        print(f"Model:   {eng.model_info.name}")
        print(f"Context: {eng.model_info.context_window // 1000}k "
              f"({eng.model_info.context_window} tokens)")
        print(f"Mode:    {eng.mode.value}")
        print(f"Budget:  {eng.ctx.budget['total_max']} tokens")
    finally:
        eng.close()
    return 0


def _cmd_reflect(world_state: str, model: str, confidence: float,
                 storage: str) -> int:
    from .engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name=model, storage_path=_storage(storage))
    try:
        _note_if_unknown(model, eng.model_info)
        result = eng.reflect(world_state=world_state, confidence=confidence)
        print(result.get("summary", ""))
        print()
        print(eng.get_state_for_injection())
    finally:
        eng.close()
    return 0


def _cmd_plugins() -> int:
    from .plugins import discover_adapters, discover_sensors, discover_tools
    for label, found in (("adapters", discover_adapters()),
                         ("sensors", discover_sensors()),
                         ("tools", discover_tools())):
        print(f"{label}:")
        if not found:
            print("  (none installed)")
        for name, obj in sorted(found.items()):
            mod = getattr(obj, "__module__", "?")
            qual = getattr(obj, "__qualname__", getattr(obj, "__name__", obj))
            print(f"  {name} -> {mod}.{qual}")
    return 0


def _cmd_set_awake(model: str, storage: str, awake: bool) -> int:
    from .engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name=model, storage_path=_storage(storage))
    try:
        eng.wake() if awake else eng.sleep()
        print(f"Awake Mode: {'ON' if eng.awake else 'OFF'} "
              f"(storage: {eng.storage})")
    finally:
        eng.close()
    return 0


def _cmd_consent(scope_arg: str, storage: str) -> int:
    from .workspace import WorkspaceContext
    from .structural_consent import (
        ConsentScope, StructuralConsent, consent_path)
    ws = WorkspaceContext().current()
    consent = StructuralConsent(consent_path(_storage(storage)))
    if scope_arg:
        scope = ConsentScope(scope_arg)
        consent.grant(ws.id, scope)
        verb = "set"
    else:
        scope = consent.scope_for(ws.id)
        verb = "current"
    print(f"structural consent {verb} for {ws.root} [{ws.id[:8]}]: {scope.value}")
    return 0


def _cmd_structure(storage: str) -> int:
    """Read-only: distill the consented graph and report drift + freshness.

    Never advances the persisted baseline (so it cannot mask drift from a running
    daemon) — it peeks at the stored baseline and computes the delta in memory.
    """
    from .workspace import WorkspaceContext
    from .structural_consent import StructuralConsent, consent_path
    from .structural import StructuralDistiller, StructuralError
    from .structural_drift import (
        StructuralDriftStore, compute_delta, compute_freshness, drift_path)

    store_dir = _storage(storage)
    ws = WorkspaceContext().current()
    consent = StructuralConsent(consent_path(store_dir))
    path = consent.graph_path_for(ws)
    tag = f"{ws.root} [{ws.id[:8]}]"
    if path is None:
        print(f"structure for {tag}: no consented graph "
              f"(scope: {consent.scope_for(ws.id).value})")
        return 0

    try:
        sig = StructuralDistiller.from_path(path).distill()
    except StructuralError as exc:
        print(f"structure for {tag}: load error: {exc}")
        return 1

    prev = StructuralDriftStore(drift_path(store_dir)).get(ws.id)   # read-only peek
    delta = compute_delta(prev, sig)
    fresh = compute_freshness(ws.root, sig.built_at_commit)

    print(f"structure for {tag}: {path}")
    print(f"  commit {sig.built_at_commit[:8] or '-'}  hash {sig.content_hash}  "
          f"nodes {sig.node_count}  hyperedges {len(sig.hyperedges)}  "
          f"communities {len(sig.communities)}")
    if fresh.is_stale:
        print(f"  freshness: STALE — graph@{(fresh.graph_commit or '')[:8]} vs "
              f"HEAD@{(fresh.head_commit or '')[:8]}")
    elif fresh.known:
        print(f"  freshness: up to date (HEAD@{(fresh.head_commit or '')[:8]})")
    else:
        print("  freshness: HEAD unknown (not a git repo / graph commit absent)")
    if delta.first_sight:
        print("  drift: first sighting (no prior baseline)")
    elif delta.changed:
        print(f"  drift: {delta.summary}")
    else:
        print("  drift: unchanged since last seen")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # bench/daemon: route the tail straight to the subcommand's own argparse so
    # flags pass through unmangled and stay in sync with that surface.
    if argv and argv[0] == "bench":
        from . import bench
        return bench.main(argv[1:])
    if argv and argv[0] == "daemon":
        from . import daemon
        return daemon.main(argv[1:])
    if argv and argv[0] == "noosphere":
        from .noosphere import cli as noosphere_cli
        return noosphere_cli.main(argv[1:])

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        return _cmd_version()
    if args.command == "info":
        return _cmd_info(args.model, args.storage)
    if args.command == "reflect":
        return _cmd_reflect(args.world_state, args.model, args.confidence,
                            args.storage)
    if args.command == "plugins":
        return _cmd_plugins()
    if args.command == "consent":
        return _cmd_consent(args.scope, args.storage)
    if args.command == "structure":
        return _cmd_structure(args.storage)
    if args.command == "awake":
        return _cmd_set_awake(args.model, args.storage, awake=True)
    if args.command == "sleep":
        return _cmd_set_awake(args.model, args.storage, awake=False)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
