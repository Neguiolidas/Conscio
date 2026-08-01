#!/usr/bin/env python3
"""Conscio DeepMiner capture hook (v3.9.1).

Records every tool call into obs.db so that aggressive context compaction stays
reversible. It runs once per tool call, so two rules dominate the design:

  1. It never imports the conscio package. ``conscio/__init__.py`` costs ~0.28s
     and pulls in an embedding model; obsstore is loaded by absolute path.
  2. It fails open, always. Any error exits 0 with no stdout, and the tool's
     output reaches the model untouched. Telemetry must never cost a session.

Dispatch is by argv because the harness sends no event name on stdin — verified
against the installed binary, see docs/reference/claude-code-harness.md 3.2.1.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

# Low on purpose: WAL handles concurrency with the MCP server, and if a write
# does not fit this budget, dropping one observation beats stalling the agent.
BUSY_TIMEOUT_MS = 400
# additionalContext is cut at 10_000 chars by the harness; stay well under it.
MAX_INJECT_CHARS = 4000


def load_config(hook_path):
    """Read the sidecar written at install time. Missing or broken -> {}."""
    try:
        data = json.loads(Path(hook_path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_obsstore(path):
    """Load conscio/obsstore.py as a standalone module, without the package."""
    spec = importlib.util.spec_from_file_location("_conscio_obsstore", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_stdin():
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _config_path(argv):
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 < len(argv):
            return argv[i + 1]
    return str(Path(__file__).with_suffix(".json"))


def main(argv):
    """Dispatch one hook event. Returns 0 unconditionally."""
    event = argv[1] if len(argv) > 1 else ""
    handler = _HANDLERS.get(event)
    if handler is None:
        return 0
    try:
        cfg = load_config(_config_path(argv))
        store = cfg.get("obsstore")
        storage = cfg.get("storage")
        if not store or not storage:
            return 0
        handler(_read_stdin(), load_obsstore(store), Path(storage))
    except Exception:
        if os.environ.get("CONSCIO_HOOK_TRACE"):
            import traceback
            traceback.print_exc(file=sys.stderr)
    return 0


_HANDLERS = {}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
