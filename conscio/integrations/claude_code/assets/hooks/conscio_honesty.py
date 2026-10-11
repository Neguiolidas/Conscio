#!/usr/bin/env python3
"""Stop hook for the honesty loop (v4.6 E2) -- SHADOW MODE.

Records outcome and does NOT contest anyone: no code path here writes to stdout.
Contestation is only enabled after the corpus gate (C5b).

The ``conscio`` package is NOT installed alongside the plugin -- it comes from
PyPI via uvx. Everything this hook uses is shipped inside the plugin assets tree
and loaded by path. Every failure exits with 0: a hook that breaks a turn is
worse than a hook that does not run.
"""

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


def _load_module(path: Path, name: str):
    """Load a vendored module by path.

    ``spec`` and ``spec.loader`` are None when the file does not exist or is not
    loadable -- exactly the case when vendoring failed. Failing here with an
    ImportError naming the path is better than an AttributeError on None three
    lines later.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"vendored module not loadable: {name} at {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--storage", required=True)
    ap.add_argument("--honesty", required=True,
                    help="directory of the honesty package (vendored)")
    ap.add_argument("--obsstore", required=True,
                    help="path to the obsstore.py MODULE, not the database")
    args = ap.parse_args(argv)

    payload = json.loads(sys.stdin.read() or "{}")
    if payload.get("stop_hook_active"):
        return 0                       # turn born from block: only observes
    text = payload.get("last_assistant_message") or ""
    session = payload.get("session_id") or ""
    if not text or not session:
        return 0

    honesty_dir = Path(args.honesty).expanduser().resolve()
    sys.path.insert(0, str(honesty_dir.parent))
    pkg = honesty_dir.name
    recognise = __import__(f"{pkg}.recognizer", fromlist=["recognise"]).recognise
    ClaimStore = __import__(f"{pkg}.store", fromlist=["ClaimStore"]).ClaimStore
    expire_stale = __import__(f"{pkg}.sweep", fromlist=["expire_stale"]).expire_stale
    obsstore = _load_module(Path(args.obsstore).expanduser().resolve(),
                            "conscio_obsstore_vendored")

    resolved_storage = obsstore.resolve_hook_storage(args.storage)
    if resolved_storage is None:
        return 0
    storage = resolved_storage
    storage.mkdir(parents=True, exist_ok=True)
    db_path = storage / "conscio.db"

    findings = recognise(obsstore.connect(storage / "obs.db"), text, session)
    if findings:
        store = ClaimStore(db_path)
        try:
            for f in findings:
                store.record(session, f.cls_name, f.anchor, f.outcome,
                             f.evidence)
        finally:
            store.close()

    # Amendment A1: stale expiration sweep runs HERE, on the only trigger that
    # already happens every turn. Without this, overdue pending claims would only
    # be derived on read and never recorded -- and expire_stale would have no
    # production caller. Only open the database if it already exists: a space that
    # never recorded an action should not get an empty conscio.db created by the hook.
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            expire_stale(conn)
        finally:
            conn.close()
    return 0                           # shadow: stdout remains empty


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        sys.exit(0)                    # fail-open and silent by design
