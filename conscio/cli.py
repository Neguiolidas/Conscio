# conscio/cli.py
"""The `conscio` command — a thin, offline-safe surface over the shipped API.

Subcommands: version | info | reflect | plugins | bench. `bench` delegates
verbatim to `conscio.bench` (no logic duplication). `info`/`reflect` build a
ConsciousnessEngine offline (no LLM, no network) and default to an ephemeral
storage dir so a quick CLI look never clobbers a real workspace.

Engine construction is deferred into the handlers, so `conscio version`,
`conscio --help`, and `conscio plugins` never build an engine.
"""
from __future__ import annotations

import argparse
import sys
import tempfile

from . import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conscio",
        description="Conscio — self-awareness framework for AI agents.")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("version", help="print the Conscio version")

    p_info = sub.add_parser("info", help="show model context window / mode / budget")
    p_info.add_argument("model", nargs="?", default="glm-5.1")
    p_info.add_argument("--storage", default="", help="storage dir (default: temp)")

    p_reflect = sub.add_parser("reflect", help="run one offline reflection cycle")
    p_reflect.add_argument("world_state", help="the world-state string to reflect on")
    p_reflect.add_argument("--model", default="glm-5.1")
    p_reflect.add_argument("--confidence", type=float, default=0.8)
    p_reflect.add_argument("--storage", default="", help="storage dir (default: temp)")

    sub.add_parser("plugins", help="list discovered adapter/sensor/tool plugins")

    # Listed for discoverability; actually routed to conscio.bench before argparse.
    sub.add_parser("bench", add_help=False,
                   help="measure an inference backend (see: conscio bench --help)")
    return parser


def _storage(arg: str) -> str:
    return arg or tempfile.mkdtemp(prefix="conscio-cli-")


def _cmd_version() -> int:
    print(__version__)
    return 0


def _cmd_info(model: str, storage: str) -> int:
    from .engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name=model, storage_path=_storage(storage))
    try:
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # bench: route the tail straight to conscio.bench (its own argparse) so flags
    # pass through unmangled and stay in sync with the bench surface.
    if argv and argv[0] == "bench":
        from . import bench
        return bench.main(argv[1:])

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

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
