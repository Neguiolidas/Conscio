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
import logging
import os
import sys
from pathlib import Path

from . import __version__

logger = logging.getLogger(__name__)

# Empty sentinel: the effective model is resolved AFTER parsing in main()
# (config.json 'model' > CONSCIO_MODEL), so a bare `conscio info` picks up the
# configured model instead of falling through to the 128k heuristic. An
# explicit CLI model still wins.
DEFAULT_MODEL = ""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conscio",
        description="Conscio — self-awareness framework for AI agents.")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("version", help="print the Conscio version")

    p_info = sub.add_parser("info", help="show model context window / mode / budget")
    p_info.add_argument("model", nargs="?", default=DEFAULT_MODEL)
    p_info.add_argument("--storage", default="", help="storage dir (default: temp)")
    p_info.add_argument("--base-url", default=None,
                        help="OpenAI-compatible endpoint to probe (e.g. http://localhost:1234/v1)")
    p_info.add_argument("--autodetect", action="store_true",
                        help="enable host-state auto-detection (config, LM Studio, GGUF)")

    p_reflect = sub.add_parser("reflect", help="run one offline reflection cycle")
    p_reflect.add_argument("world_state", help="the world-state string to reflect on")
    p_reflect.add_argument("--model", default=DEFAULT_MODEL)
    p_reflect.add_argument("--confidence", type=float, default=0.8)
    p_reflect.add_argument("--storage", default="", help="storage dir (default: temp)")
    p_reflect.add_argument("--base-url", default=None,
                           help="OpenAI-compatible endpoint to probe")
    p_reflect.add_argument("--autodetect", action="store_true",
                           help="enable host-state auto-detection")
    p_reflect.add_argument("--mode", default="compact",
                           choices=["minimal", "compact", "full"],
                           help="output verbosity (default: compact)")

    # v3.7: council subcommand — convene 4-voice council from CLI
    p_council = sub.add_parser("council", help="convene a 4-voice council")
    p_council.add_argument("question", help="the decision question")
    p_council.add_argument("--context", default="", help="optional context string")
    p_council.add_argument("--options", default="", help="comma-separated options")
    p_council.add_argument("--model", default=DEFAULT_MODEL)
    p_council.add_argument("--storage", default="", help="storage dir (default: temp)")
    p_council.add_argument("--mode", default="compact",
                           choices=["minimal", "compact", "full"],
                           help="output verbosity (default: compact)")

    p_govern = sub.add_parser("govern", help="measure and cap the context window")
    p_govern.add_argument("action",
                          choices=["prefix", "on", "off", "status", "report"])
    p_govern.add_argument("--window", type=int, default=None,
                          help="context ceiling in tokens (default: measured)")
    p_govern.add_argument("--all", action="store_true", dest="all_sessions",
                          help="report: across sessions, not just this one")
    p_govern.add_argument("--storage", default=None)

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

    p_trial = sub.add_parser(
        "trial",
        help="trial a quarantined imported skill in a throwaway sandbox")
    p_trial.add_argument("--storage", default="",
                         help="instance storage dir (default: ~/.hermes)")
    p_trial.add_argument("--quarantine", type=int, required=True,
                         metavar="ROWID", help="quarantine row id to trial")
    p_trial.add_argument("--model", default=DEFAULT_MODEL)
    p_trial.add_argument(
        "--enable-trial", action="store_true",
        help="required: actually run the sandboxed trial (off by default)")

    p_promote = sub.add_parser(
        "promote",
        help="promote a trialed quarantined skill into the live library")
    p_promote.add_argument("--storage", default="",
                           help="instance storage dir (default: ~/.hermes)")
    p_promote.add_argument("--quarantine", type=int, required=True,
                           metavar="ROWID",
                           help="quarantine row id to promote")
    p_promote.add_argument(
        "--enable-promote", action="store_true",
        help="required: actually write to the live library (off by default)")
    p_promote.add_argument("--model", default=DEFAULT_MODEL,
                           help="model name for engine init")

    # Listed for discoverability; routed to conscio.{bench,daemon} before argparse.
    sub.add_parser("bench", add_help=False,
                   help="measure an inference backend (see: conscio bench --help)")
    sub.add_parser("daemon", add_help=False,
                   help="run the live heartbeat (see: conscio-daemon --help)")
    sub.add_parser("noosphere", add_help=False,
                   help="share skills across same-host instances "
                        "(see: conscio noosphere --help)")

    p_ingest = sub.add_parser(
        "ingest",
        help="ingest a file or directory into ContentStore (+ vector search)")
    p_ingest.add_argument("path", help="file or directory to ingest (recursive)")
    p_ingest.add_argument("--category", default="reference",
                          help="ContentStore category (default: reference)")
    p_ingest.add_argument("--chunk-size", type=int, default=2000,
                          help="max chars per chunk (default: 2000)")
    p_ingest.add_argument("--overlap", type=float, default=0.2,
                          help="fraction of chunk_size to overlap between "
                               "chunks (default: 0.2)")
    p_ingest.add_argument("--model", default=DEFAULT_MODEL)
    p_ingest.add_argument("--storage", default="",
                          help="storage dir (default: ~/.hermes)")

    p_search = sub.add_parser(
        "search",
        help="search ContentStore (FTS5 + optional vector)")
    p_search.add_argument("query", help="search query")
    p_search.add_argument("--k", type=int, default=5,
                          help="max results (default: 5)")
    p_search.add_argument("--category", default=None,
                          help="filter by category")
    p_search.add_argument("--storage", default="",
                          help="storage dir (default: ~/.hermes)")
    p_search.add_argument("--model", default=DEFAULT_MODEL)
    p_search.add_argument("--include-stale", action="store_true",
                          help="include tombstoned chunks")
    p_search.add_argument("--exact", "-e", action="store_true",
                          help="activate trigram index for exact substring match")

    p_manual = sub.add_parser(
        "manual",
        help="print the location of the usage manual (USAGE.md shipped with the package)")
    p_manual.add_argument(
        "--open", action="store_true",
        help="also try to open the manual with the system pager/editor")

    # v3.5: observatory subcommand
    p_obs = sub.add_parser(
        "observatory",
        help="start the read-only Observatory web UI (loopback only)")
    p_obs.add_argument("--host", default="127.0.0.1",
                       help="bind host (loopback only, default: 127.0.0.1)")
    p_obs.add_argument("--port", type=int, default=8788,
                       help="bind port (default: 8788)")
    p_obs.add_argument("--root", default=".",
                       help="workspace root for graphify-out/graph.html (default: cwd)")
    p_obs.add_argument("--token", default=None,
                       help="optional bearer token for API access")
    p_obs.add_argument("--storage", default="",
                       help="storage dir (default: ~/.hermes)")
    p_obs.add_argument("--noosphere", default="",
                       help="noosphere.db path (default: ~/.hermes/noosphere.db)")
    p_obs.add_argument("--liaison-db", default="",
                       help="liaison.db path (default: ~/.hermes/liaison.db)")
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


def _cmd_info(model: str, storage: str,
              base_url: str | None = None, autodetect: bool = True) -> int:
    from .engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name=model, storage_path=_storage(storage),
                               base_url=base_url, autodetect=autodetect)
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
                 storage: str, mode: str = "compact",
                 base_url: str | None = None, autodetect: bool = True) -> int:
    from .engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name=model, storage_path=_storage(storage),
                               base_url=base_url, autodetect=autodetect)
    try:
        _note_if_unknown(model, eng.model_info)
        result = eng.reflect(world_state=world_state, confidence=confidence)

        if mode == "minimal":
            print(result.get("summary", ""))
        elif mode == "compact":
            print(result.get("summary", ""))
            print()
            state_lines = eng.get_state_for_injection().split("\n")
            print("\n".join(state_lines[:8]))  # top 8 lines apenas
        else:
            print(result.get("summary", ""))
            print()
            print(eng.get_state_for_injection())
    finally:
        eng.close()
    return 0


def _cmd_council(question: str, context: str, options: str, model: str,
                 storage: str, mode: str) -> int:
    """Convene a 4-voice council from the CLI."""
    from .engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name=model, storage_path=_storage(storage))
    try:
        opts = [o.strip() for o in options.split(",") if o.strip()] if options else None
        result = eng.council(question=question, context=context, options=opts)

        # Detect mode: deterministic vs llm
        has_llm = any(
            "LLM" in v.get("analysis", "")
            for v in result.get("voices", [])
        )
        active_mode = "llm" if has_llm else "deterministic"

        if mode == "minimal":
            print(f"mode: {active_mode}")
            print(f"recommendation: {result['recommendation']}")
            print(f"votes: {result['votes_summary']}")
        elif mode == "compact":
            print(f"mode: {active_mode}")
            print(f"recommendation: {result['recommendation']}")
            print(f"votes: {result['votes_summary']}")
            for v in result.get("voices", []):
                top = v.get("concerns", ["none"][:1])
                top = top[0] if top else "none"
                print(f"  {v['role']}: vote={v['vote']}, top_concern={top}")
        else:  # full
            import json
            print(json.dumps(result, indent=2, default=str))
    finally:
        eng.close()
    return 0


def _cmd_govern(action: str, window: int | None, storage: str,
                all_sessions: bool) -> int:
    """Measure, apply, or report on the context ceiling."""
    from . import governor
    from .timeutil import naive_utcnow
    space = _storage(storage)
    ts = naive_utcnow().strftime("%Y%m%d_%H%M%S")

    def _profile():
        rows: list[dict] = []
        for path in governor._recent_transcripts(governor.projects_dir(), 10):
            rows.extend(governor.read_usage(path))
        out_rate = (sum(r["out"] for r in rows) / len(rows)) if rows else 0.0
        growth = governor.growth_per_session(governor.projects_dir())
        return rows, governor.summarise(rows), growth, out_rate

    if action in ("prefix", "status"):
        measured = governor.measure_prefix(governor.projects_dir())
        prefix = measured["prefix"]
        active = governor.current_window()
        print(f"Stable prefix   {prefix:>10,}  "
              f"(median first turn of {measured['sessions']} sessions)")
        print(f"Ceiling         "
              f"{'OFF' if active is None else format(active, ',')}")
        if action == "prefix":
            _, agg, growth, out_rate = _profile()
            landed = governor.compaction_floor(governor.projects_dir())
            print(f"Growth rate     {growth:>10,.0f}  tokens added per request")
            print(f"Compaction lands{landed:>10,}  "
                  f"{'(never observed)' if not landed else '(worst seen)'}")
            floor = governor.hard_floor(prefix, landed)
            print(f"Refused below   {floor:>10,}")
            best = governor.recommend_window(
                prefix, requests=agg["requests"], growth=growth,
                out_per_request=out_rate, floor=landed)
            print(f"Recommended     {best:>10,}  (cheapest window above the floor)")
            print("\n  window      modelled cost")
            for w in governor.CANDIDATE_WINDOWS:
                if w < floor:
                    # Never print a cost here. modelled_cost assumes a compaction
                    # reclaims room; below the floor it reclaims nothing, so the
                    # number would be both wrong and the cheapest on the page.
                    if w <= prefix:
                        why = "cannot hold the prefix"
                    elif w < int(landed * governor.FLOOR_MARGIN):
                        why = "compaction would loop"
                    else:
                        why = "too little room above prefix"
                    print(f"  {w:>9,}  {'refused':>14}  ({why})")
                    continue
                cost = governor.modelled_cost(
                    w, prefix=prefix, requests=agg["requests"], growth=growth,
                    out_per_request=out_rate)
                mark = "  <- recommended" if w == best else ""
                print(f"  {w:>9,}  {cost:>14,.0f}{mark}")
        else:
            obs = Path(space) / "obs.db"
            size = obs.stat().st_size if obs.exists() else 0
            print(f"obs.db          {size / 1_048_576:>10,.1f} MB")
            print(f"Baseline        "
                  f"{'recorded' if governor.read_baseline(space) else 'none'}")
        return 0

    if action == "on":
        measured = governor.measure_prefix(governor.projects_dir())
        prefix = measured["prefix"]
        landed = governor.compaction_floor(governor.projects_dir())
        _, agg, growth, out_rate = _profile()
        if prefix <= 0:
            print("Refusing: no transcripts to measure from, so there is no "
                  "prefix to size a window against.")
            print(f"  Looked in {governor.projects_dir()}.")
            print("  Run a session first, or pass --window explicitly if you "
                  "know what your prefix is.")
            return 1
        chosen = window or governor.recommend_window(
            prefix, requests=agg["requests"], growth=growth,
            out_per_request=out_rate, floor=landed)
        room_floor = int(prefix * governor.MIN_HEADROOM_FACTOR)
        land_floor = int(landed * governor.FLOOR_MARGIN)
        if chosen < max(room_floor, land_floor):
            print(f"Refusing: window {chosen:,} cannot hold.")
            if chosen < room_floor:
                print(f"  Measured prefix {prefix:,} leaves no working room "
                      f"below {room_floor:,} "
                      f"(prefix x{governor.MIN_HEADROOM_FACTOR:g}).")
            if chosen < land_floor:
                print(f"  Compaction lands at {landed:,} in your own "
                      f"transcripts. A window under {land_floor:,} would "
                      f"compact, land above itself, and compact again.")
            print("  Either raise --window, or prune the prefix: "
                  "`conscio govern prefix` lists what feeds it.")
            return 1
        governor.write_baseline(space, governor.snapshot(space))
        backup = governor.apply_window(chosen, ts=ts)
        print(f"Ceiling set to {chosen:,} tokens (prefix {prefix:,}).")
        if backup:
            print(f"  Settings backed up to {backup}")
        print("  Baseline frozen. Revert with `conscio govern off`.")
        return 0

    if action == "off":
        base = governor.read_baseline(space) or {}
        prior = base.get("prior_window")
        governor.clear_window(ts=ts, previous=prior)
        print("Ceiling removed." if prior is None
              else f"Ceiling restored to your previous {prior:,}.")
        print("  The baseline is kept for reporting.")
        return 0

    if action == "report":
        if all_sessions:
            print(governor.report_all(space, governor.current_window()))
            return 0
        paths = governor._recent_transcripts(governor.projects_dir(), 1)
        if not paths:
            print("No sessions found under " + str(governor.projects_dir()))
            return 0
        print(governor.report_for_session(paths[0], space,
                                          governor.current_window()))
        return 0
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
    from .structural_consent import ConsentScope, StructuralConsent, consent_path
    from .workspace import WorkspaceContext
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
    from .structural import StructuralDistiller, StructuralError
    from .structural_consent import StructuralConsent, consent_path
    from .structural_drift import (
        StructuralDriftStore,
        compute_delta,
        compute_freshness,
        drift_path,
    )
    from .workspace import WorkspaceContext

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


def _run_trial(*, model: str, storage: str, quarantine_id: int,
               enable_trial: bool):
    """Build an engine with an adapter and run one trial. The single seam the
    CLI tests monkeypatch (so they never build a real adapter)."""
    from .adapter_config import build_adapter_from_config, load_config
    from .engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name=model, storage_path=_storage(storage))
    try:
        adapter, _ = build_adapter_from_config(load_config(),
                                               fallback_model=model)
        eng.attach_adapter(adapter)
        return eng.trial_quarantined(quarantine_id, enable_trial=enable_trial)
    finally:
        eng.close()


def _cmd_trial(model: str, storage: str, quarantine_id: int,
               enable_trial: bool) -> int:
    from .agency.trial import TrialRefusal
    try:
        outcome = _run_trial(model=model, storage=storage,
                             quarantine_id=quarantine_id,
                             enable_trial=enable_trial)
    except Exception as exc:               # adapter build / engine wiring failure
        print(f"error: {exc}")
        return 1
    if isinstance(outcome, TrialRefusal):
        print(f"error: {outcome.reason}")
        return 1
    if outcome.passed:
        print(f"TRIAL PASSED (#{quarantine_id})")
    else:
        print(f"TRIAL FAILED (#{quarantine_id}): {outcome.result}")
        if outcome.error:
            print(f"  {outcome.error}")
    # best-effort: show the running counts (skipped if the row can't be read)
    try:
        from .noosphere import quarantine
        from .noosphere.paths import quarantine_db_path
        row = quarantine.get(quarantine_db_path(Path(_storage(storage))),
                             quarantine_id)
        if row is not None:
            print(f"  trials: {row.trial_successes} passed / "
                  f"{row.trial_failures} failed")
    except Exception:
        pass
    return 0


def _run_promote(*, model: str, storage: str, quarantine_id: int,
                 enable_promote: bool):
    """Build a bare engine (no adapter — promotion never decodes) and promote
    one quarantined skill. The single seam the CLI tests monkeypatch."""
    from .engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name=model,
                              storage_path=_storage(storage))
    try:
        return eng.promote_quarantined(quarantine_id,
                                       enable_promote=enable_promote)
    finally:
        eng.close()


def _cmd_promote(model: str, storage: str, quarantine_id: int,
                 enable_promote: bool) -> int:
    from .agency.promote import PromoteResult
    try:
        outcome = _run_promote(model=model, storage=storage,
                               quarantine_id=quarantine_id,
                               enable_promote=enable_promote)
    except Exception as exc:               # engine wiring failure
        print(f"error: {exc}")
        return 1
    if isinstance(outcome, PromoteResult):
        print(f"PROMOTED skill #{outcome.skill_id} "
              f"(seeded {outcome.successes}/{outcome.failures})")
        return 0
    print(f"PROMOTE REFUSED: {outcome.reason}")
    return 1


def _cmd_ingest(path: str, category: str, chunk_size: int, overlap: float,
                model: str, storage: str) -> int:
    """Ingest a file/directory into ContentStore via engine.ingest_directory().

    Prints a simple progress line every 500 files, then emits a `host:event`
    on the EventBus with the ingest summary (best-effort — never fails the
    command if the emit itself raises).
    """
    from .engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name=model, storage_path=_storage(storage))
    try:
        def _progress(processed: int, total: int) -> None:
            if total and (processed % 500 == 0 or processed == total):
                print(f"  ingest: {processed}/{total} files processed")

        result = eng.ingest_directory(
            path, category=category, chunk_size=chunk_size, overlap=overlap,
            progress_callback=_progress,
        )
        print(f"ingest complete: {result['ingested']} ingested, "
              f"{result.get('duplicate', 0)} duplicate, "
              f"{result['skipped']} skipped, {result['failed']} failed "
              f"of {result['total']} files ({result['duration_s']}s)")
        # NFR evidence, printed by the run that produces it: total on-disk
        # footprint (conscio.db + vectors.db + sidecars) and how many chunks
        # actually reached the vector index. Without these, "did the ingest
        # meet NFR2 / did embeddings really happen" needs a separate probe.
        try:
            st = eng.content_store.stats()
            line = (f"  store: {st['chunk_count']} chunks, "
                    f"{st['db_size_mb']}MB total on disk")
            if "vector_count" in st:
                line += (f" ({st['vector_db_size_mb']}MB vectors, "
                         f"{st['vector_count']} embedded)")
            else:
                line += " (vector backend off)"
            print(line)
        except Exception:
            logger.warning("_cmd_ingest: stats readout failed", exc_info=True)
        try:
            eng.event_bus.emit(
                type="host:event", category="system",
                data={"event": "ingest_directory", "path": str(path),
                      "category": category, **result},
            )
        except Exception:
            logger.warning("_cmd_ingest: event_bus.emit failed", exc_info=True)
    finally:
        eng.close()
    return 0


def _cmd_search(query: str, k: int, category: str | None,
                 storage: str, model: str, include_stale: bool,
                 exact: bool = False) -> int:
    """Search ContentStore and print results."""
    from .engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name=model, storage_path=_storage(storage))
    try:
        results = eng.content_store.search(
            query, limit=k, category=category, include_stale=include_stale,
            use_trigram=exact,
        )
        if not results:
            print("(no results)")
            return 0
        for i, r in enumerate(results):
            snippet = r.content[:120].replace("\n", " ").strip()
            print(f"[{i+1}] {r.title} (score={r.rank:.3f})")
            print(f"    {snippet}")
        return 0
    finally:
        eng.close()


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
    if argv and argv[0] == "init":
        from .installer import cli as installer_cli
        return installer_cli.main(argv[1:])

    parser = _build_parser()
    args = parser.parse_args(argv)

    # Resolve the model when a subcommand didn't get one explicitly: config.json
    # 'model' then CONSCIO_MODEL (matches the daemon/MCP precedence). Left empty
    # if nothing is configured — the subcommand degrades gracefully (info warns).
    if getattr(args, "model", None) == "":
        from .adapter_config import load_config
        from .models import resolve_model_name
        try:
            args.model = resolve_model_name(config_model=load_config().get("model"))
        except ValueError:
            args.model = ""

    if args.command == "version":
        return _cmd_version()
    if args.command == "info":
        return _cmd_info(args.model, args.storage,
                         base_url=args.base_url, autodetect=args.autodetect)
    if args.command == "reflect":
        return _cmd_reflect(args.world_state, args.model, args.confidence,
                            args.storage, args.mode,
                            base_url=args.base_url, autodetect=args.autodetect)
    if args.command == "council":
        return _cmd_council(args.question, args.context, args.options,
                           args.model, args.storage, args.mode)
    if args.command == "govern":
        return _cmd_govern(args.action, args.window, args.storage,
                           args.all_sessions)
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
    if args.command == "trial":
        return _cmd_trial(args.model, args.storage, args.quarantine,
                          args.enable_trial)
    if args.command == "promote":
        return _cmd_promote(args.model, args.storage, args.quarantine,
                            args.enable_promote)
    if args.command == "ingest":
        return _cmd_ingest(args.path, args.category, args.chunk_size,
                           args.overlap, args.model, args.storage)
    if args.command == "search":
        return _cmd_search(args.query, args.k, args.category,
                           args.storage, args.model, args.include_stale,
                           exact=args.exact)
    if args.command == "manual":
        return _cmd_manual(open_it=getattr(args, "open", False))
    if args.command == "observatory":
        return _cmd_observatory(host=args.host, port=args.port,
                                root=args.root, token=args.token,
                                storage=args.storage,
                                noosphere=args.noosphere,
                                liaison_db=args.liaison_db)
    parser.print_help()
    return 2


def _cmd_observatory(*, host: str, port: int, root: str,
                     token: str | None, storage: str,
                     noosphere: str = "", liaison_db: str = "") -> int:
    """Start the read-only Observatory web UI (loopback only)."""
    from pathlib import Path

    from .observatory.server import _DEFAULT_LIAISON, _DEFAULT_NOOSPHERE, make_server
    if storage:
        storage_path = Path(storage).expanduser()
    else:
        storage_path = Path.home() / ".hermes" / "consciousness"
    noo = Path(noosphere).expanduser() if noosphere else _DEFAULT_NOOSPHERE
    liai = Path(liaison_db).expanduser() if liaison_db else _DEFAULT_LIAISON
    root_abs = str(Path(root).expanduser().resolve())
    srv = make_server(host, port, token, storage_path, noo, liai,
                      workspace_root=root_abs)
    print(f"Conscio Observatory: http://{host}:{port}")
    print(f"  workspace root: {root_abs}")
    print(f"  graph view: http://{host}:{port}/graph")
    print("  Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down...")
        srv.shutdown()
    return 0


def _cmd_manual(*, open_it: bool = False) -> int:
    """Locate and optionally open the USAGE.md manual shipped with the package."""
    import subprocess
    from pathlib import Path
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "USAGE.md",
        here / "USAGE.md",
        here.parent / "docs" / "guides" / "mcp.md",
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        print("Manual not found. See https://github.com/Neguiolidas/Conscio#readme")
        return 1
    print(f"[conscio] manual: {path}")
    if not open_it:
        print("Use --open to page through it now.")
        return 0
    pager = os.environ.get("PAGER") or os.environ.get("EDITOR") or "less"
    try:
        subprocess.run([pager, str(path)], check=False)
    except FileNotFoundError:
        print(path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
