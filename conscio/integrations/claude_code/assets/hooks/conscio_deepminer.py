#!/usr/bin/env python3
"""Conscio DeepMiner capture hook (v3.9.1).

Records every tool call into obs.db so that aggressive context compaction stays
reversible. It runs once per tool call, so two rules dominate the design:

  1. It never imports the conscio package. ``conscio/__init__.py`` costs ~0.28s
     and pulls in an embedding model; obsstore is loaded by absolute path.
  2. It fails open, always. Any error exits 0 with no stdout, and the tool's
     output reaches the model untouched. Telemetry must never cost a session.

Dispatch is by argv. The harness does send ``hook_event_name`` on stdin, but the
registration in settings.json is per event anyway, so argv keeps the dispatch
visible in the registered command instead of buried in the payload. See
docs/reference/claude-code-harness.md 3.2.1 -- including the correction to the
earlier claim that no event name existed, which came from a broken measurement.
"""
import datetime
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


def _argv_opt(argv, flag):
    """Value of ``--flag <value>`` in argv, or None. argv beats the sidecar."""
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _config_path(argv):
    return _argv_opt(argv, "--config") or str(Path(__file__).with_suffix(".json"))


def _project_root(cwd):
    """Repo root enclosing ``cwd``, or ``cwd`` itself when it is not in a repo.

    ``project`` is the scope key for recall, so it has to be the same string
    from every subdirectory. Storing the raw cwd split one repository into six
    "projects" and a scope="project" search from a subdirectory saw a fraction
    of its own rows.

    Walks up looking for a ``.git`` *entry* rather than a directory: a worktree
    or submodule carries a ``.git`` file, and each of those is its own project.
    No subprocess — the hook runs on a bare interpreter and must stay cheap.
    """
    try:
        here = Path(cwd).resolve()
    except (OSError, ValueError):
        return str(cwd)
    for d in (here, *here.parents):
        try:
            if (d / ".git").exists():
                return str(d)
        except OSError:
            break                       # unreadable ancestor: stop walking up
    return str(here)


# ``agent_label`` deliberately lives in obsstore, not here: the engine needs the
# same resolution, and this hook loads obsstore by absolute path, so one
# implementation serves both and the vendored copy keeps them in step.


def main(argv):
    """Dispatch one hook event. Returns 0 unconditionally."""
    event = argv[1] if len(argv) > 1 else ""
    handler = _HANDLERS.get(event)
    if handler is None:
        return 0
    try:
        cfg = load_config(_config_path(argv))
        store = _argv_opt(argv, "--obsstore") or cfg.get("obsstore")
        storage = _argv_opt(argv, "--storage") or cfg.get("storage")
        if not store or not storage:
            return 0
        if (Path(storage) / "capture-off").exists():  # muted for this space
            return 0
        handler(_read_stdin(), load_obsstore(store), Path(storage))
    except Exception:
        if os.environ.get("CONSCIO_HOOK_TRACE"):
            import traceback
            traceback.print_exc(file=sys.stderr)
    return 0


def _utc_now():
    """Naive UTC, matching conscio.timeutil.naive_utcnow().

    The engine writes this column too, and obsstore.prune() builds its cutoff
    from datetime.now(timezone.utc).replace(tzinfo=None). A hook writing local
    time would put two clocks in one column: west of UTC every hook row would
    read as hours older than it is, and rows from the two writers would not sort
    against each other.
    """
    return datetime.datetime.now(
        datetime.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _as_text(value):
    """Flatten a tool_input/tool_response field to storable text.

    tool_response is a dict whose shape varies per tool ({"stdout":...} for Bash,
    {"success":true} for Write). Storing the JSON keeps every field rather than
    guessing which one matters.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def on_tool(payload, store, storage, failed=False):
    """Record one tool call. Called on both success and failure events."""
    tool = str(payload.get("tool_name") or "unknown")
    if failed:
        # Kept in the tool name so a failure is visible in any recall, without
        # a schema column that only this one event would ever set.
        tool += "!failed"
    # The budget has to be handed to connect(), not set after it: opening the
    # store can itself contend for the lock, and by then it is too late.
    conn = store.connect(storage / "obs.db", busy_timeout_ms=BUSY_TIMEOUT_MS)
    try:
        store.put_observation(
            conn,
            tool=tool,
            input_text=_as_text(payload.get("tool_input")),
            output_text=_as_text(payload.get("tool_response")),
            project=_project_root(payload.get("cwd") or os.getcwd()),
            agent=store.agent_label(storage),
            session_id=str(payload.get("session_id") or "unknown"),
            ts=_utc_now(),
        )
    finally:
        conn.close()


# Retention runs at session start, never on the hot path.
RETENTION_DAYS = 30
RETENTION_BYTES = 2 * 1024 ** 3


def render_index(summary):
    """One short block naming what the last session did — never its content.

    additionalContext is capped by the harness, and the whole point of the store
    is that content stays out of context until asked for. So this says what
    exists and how to fetch it, and nothing else.
    """
    if not summary or not summary["total"]:
        return ""
    tools = ", ".join(f"{name} x{count}" for name, count in summary["tools"][:8])
    return (
        f"Conscio DeepMiner: {summary['total']} tool calls recorded in the "
        f"previous session ({summary['session_id']}), {summary['first_ts']} to "
        f"{summary['last_ts']}. Tools: {tools}. "
        f"Search them with conscio_recall_observations "
        f'(scope="all" to reach past sessions).'
    )[:MAX_INJECT_CHARS]


def on_session_start(payload, store, storage):
    """Prune, then inject an index of the previous session."""
    current = str(payload.get("session_id") or "")
    conn = store.connect(storage / "obs.db")
    try:
        try:
            store.prune(conn, max_age_days=RETENTION_DAYS,
                        max_bytes=RETENTION_BYTES)
        except Exception:
            pass  # retention is best-effort; never block a session on it
        prev = store.last_session_id(conn, exclude=current)
        if not prev:
            return
        text = render_index(store.session_summary(conn, prev))
        if text:
            sys.stdout.write(text + "\n")
    finally:
        conn.close()


COMPACT_INSTRUCTIONS = (
    "Preserve: the task in progress and why, files opened or edited and what "
    "changed in each, decisions taken and the reasoning behind them, and any "
    "failing test or unresolved error with its exact message. Drop: tool output "
    "that has been superseded, and narration. Every tool call in this session is "
    "recorded verbatim in Conscio and can be recovered with "
    "conscio_recall_observations, so summarise freely rather than hoarding detail."
)


def on_pre_compact(payload, store, storage):
    """Steer the summariser and mark the boundary. Never blocks.

    PreCompact can block compaction; this deliberately does not. The whole point
    of the Governor is to compact more often, so blocking would work against it.

    The steer goes out as bare text, not as the ``hookSpecificOutput`` envelope
    every other event uses. PreCompact has no variant in that union: the host
    keeps the successful hooks whose output is non-empty, joins their stdout with
    newlines, and hands the result to the summariser as its instructions. An
    envelope here would be the instructions -- a JSON blob where prose belongs.
    """
    session = str(payload.get("session_id") or "unknown")
    # The steer goes out before the store is touched. It needs nothing from the
    # database, and this handler runs inside a fail-open wrapper: writing it last
    # would let a locked or unwritable obs.db silently cost the one thing here
    # that changes what the model keeps. The boundary marker is the expendable
    # half, so it is the half that runs second.
    sys.stdout.write(COMPACT_INSTRUCTIONS + "\n")
    conn = store.connect(storage / "obs.db", busy_timeout_ms=BUSY_TIMEOUT_MS)
    try:
        store.put_observation(
            conn, tool="compact-boundary", input_text="",
            output_text=f"compaction started for session {session}",
            project=_project_root(payload.get("cwd") or os.getcwd()),
            agent=store.agent_label(storage), session_id=session, ts=_utc_now())
    finally:
        conn.close()


def on_post_compact(payload, store, storage):
    """Store the summary the host produced, then point at what it replaced."""
    session = str(payload.get("session_id") or "unknown")
    # The field is ``compact_summary``. Reading ``summary`` matched nothing, so
    # the one artefact worth keeping across a compaction was dropped in silence
    # -- and silence is what a missing key always looks like here. Both names are
    # accepted because this contract belongs to the host, not to us.
    summary = _as_text(payload.get("compact_summary") or payload.get("summary"))
    conn = store.connect(storage / "obs.db", busy_timeout_ms=BUSY_TIMEOUT_MS)
    try:
        if summary:
            store.put_observation(
                conn, tool="compact-summary", input_text="",
                output_text=summary,
                project=_project_root(payload.get("cwd") or os.getcwd()),
                agent=store.agent_label(storage), session_id=session, ts=_utc_now())
        info = store.session_summary(conn, session)
    finally:
        conn.close()
    if not info["total"]:
        return
    # The summary is already in context. What the model does not know is that the
    # detail behind it survived, and how to ask for it.
    sys.stdout.write(
        f"Conscio: {info['total']} tool calls from this session are stored "
        f"verbatim and survived compaction. Recover any of them with "
        f"conscio_recall_observations before assuming detail was lost.\n")


_HANDLERS = {
    "session-start": on_session_start,
    "post-tool-use": lambda p, s, d: on_tool(p, s, d, failed=False),
    "post-tool-use-failure": lambda p, s, d: on_tool(p, s, d, failed=True),
    "pre-compact": on_pre_compact,
    "post-compact": on_post_compact,
}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
