"""T2: the Codex hooks (Codex plan §3.3, amended §6) — host detection, the
SessionStart/Stop subcommands, the owner-pid walk-up and the JSON instruction
line. The reactor core is T1 (test_codex_reactor.py); here we test the hook
surface that arms it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parents[1]
    / "conscio" / "integrations" / "claude_code" / "assets" / "hooks"
    / "conscio_wake.py"
)
SELF_ID = "self-cx-0001"
SESSION_C = "codex-sess-0001"


def _hook():
    spec = importlib.util.spec_from_file_location("_codex_hooks_hook", HOOK)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_space(tmp_path: Path) -> Path:
    space = tmp_path / "space"
    (space / "reactors").mkdir(parents=True)
    (space / "spool" / SELF_ID).mkdir(parents=True)
    db = space / "liaison.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, from_instance TEXT, "
        "to_instance TEXT, type TEXT, payload TEXT, ts REAL, read_ts REAL)")
    conn.commit()
    conn.close()
    (space / "instance.json").write_text(
        json.dumps({"instance_id": SELF_ID}), encoding="utf-8")
    return space


def _codex_env(tmp_path: Path, codex_sessions: Path, codex_bin: str | None) -> dict:
    env = dict(os.environ)
    env["CONSCIO_SELF_ID"] = SELF_ID
    env["CONSCIO_CODEX_SESSIONS"] = str(codex_sessions)
    env.pop("CONSCIO_CODEX_BIN", None)
    if codex_bin is not None:
        env["CONSCIO_CODEX_BIN"] = codex_bin
    # no CODEX_* host-marker env is set: detection must come from the payload
    for key in list(env):
        if key.startswith("CODEX_"):
            del env[key]
    return env


# ── host detection (pure) ────────────────────────────────────────────────────

def test_is_codex_host_transcript_under_the_root(tmp_path):
    root = tmp_path / "codex-sessions"
    root.mkdir()
    assert _hook().is_codex_host(
        {"session_id": "s", "transcript_path": str(root / "a.json")},
        str(root)) is True


def test_is_codex_host_transcript_outside_the_root(tmp_path):
    root, other = tmp_path / "codex-sessions", tmp_path / "elsewhere"
    root.mkdir(); other.mkdir()
    assert _hook().is_codex_host(
        {"session_id": "s", "transcript_path": str(other / "a.json")},
        str(root)) is False


def test_is_codex_host_requires_a_session_and_transcript(tmp_path):
    root = tmp_path / "codex-sessions"
    assert _hook().is_codex_host({"session_id": "s"}, str(root)) is False
    assert _hook().is_codex_host({"transcript_path": str(root / "a.json")},
                                 str(root)) is False
    assert _hook().is_codex_host(None, str(root)) is False


# ── the SessionStart JSON output shape ───────────────────────────────────────

def test_codex_session_start_emits_the_json_instruction(tmp_path):
    space = _make_space(tmp_path)
    sessions = tmp_path / "codex-sessions"
    sessions.mkdir()
    wrapper = tmp_path / "fake-codex"
    wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    wrapper.chmod(0o755)
    env = _codex_env(tmp_path, sessions, str(wrapper))
    payload = {"session_id": SESSION_C, "transcript_path": str(sessions / "t.json")}
    r = subprocess.run(
        [sys.executable, str(HOOK), "codex_session_start", "--storage", str(space)],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = json.loads(r.stdout.strip())
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "relay:" in ctx and "inbox" in ctx


def test_codex_session_start_is_a_noop_off_codex(tmp_path):
    space = _make_space(tmp_path)
    sessions, other = tmp_path / "codex-sessions", tmp_path / "not-codex"
    sessions.mkdir(); other.mkdir()
    env = _codex_env(tmp_path, sessions, None)
    # a transcript OUTSIDE the codex sessions root: not a Codex host -> no JSON
    payload = {"session_id": SESSION_C, "transcript_path": str(other / "t.json")}
    r = subprocess.run(
        [sys.executable, str(HOOK), "codex_session_start", "--storage", str(space)],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0
    assert not r.stdout.strip()          # no instruction line, no hookSpecificOutput


# ── the owner-pid walk-up: no codex ancestor -> no spawn ────────────────────

def test_no_codex_ancestor_means_no_spawn(tmp_path):
    space = _make_space(tmp_path)
    sessions = tmp_path / "codex-sessions"
    sessions.mkdir()
    wrapper = tmp_path / "fake-codex"
    wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    wrapper.chmod(0o755)
    env = _codex_env(tmp_path, sessions, str(wrapper))
    payload = {"session_id": SESSION_C, "transcript_path": str(sessions / "t.json")}
    r = subprocess.run(
        [sys.executable, str(HOOK), "codex_session_start", "--storage", str(space)],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0
    # no codex ancestor in this test's process tree -> the reactor is NOT spawned
    assert not (space / "reactors" / f"{SESSION_C}.log").exists()
    time.sleep(0.3)
    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True)
    assert ("react" in " ".join(
        [l for l in ps.stdout.splitlines() if SESSION_C in l and "conscio_wake.py" in l])) is False


# ── the Stop never blocks ───────────────────────────────────────────────────

def test_codex_stop_never_blocks_and_exits_zero(tmp_path):
    space = _make_space(tmp_path)
    sessions = tmp_path / "codex-sessions"
    sessions.mkdir()
    env = _codex_env(tmp_path, sessions, None)
    payload = {"session_id": SESSION_C, "transcript_path": str(sessions / "t.json")}
    start = time.time()
    r = subprocess.run(
        [sys.executable, str(HOOK), "codex_stop", "--storage", str(space)],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert time.time() - start < 10     # it does not block the Codex turn


# ── the storage resolver: the single resolver on the MCP server's own inputs ─

def test_storage_mcp_inprocess_resolves_the_server_space(tmp_path):
    """(Claude 529-1) with no harness --storage the hook resolves the space via
    the SINGLE resolver (conscio.installer.durable.resolve_space) on the MCP
    server's own --storage from .mcp.json. Here the package is importable in
    this python, so the in-process importlib guard takes it (no second copy)."""
    space = _make_space(tmp_path)
    mcp = tmp_path / "mcp.json"
    # a runner command that CANNOT resolve (nonexistent): the in-process guard
    # is the only path that can succeed here, so this test isolates it.
    mcp.write_text(json.dumps({"mcpServers": {"conscio": {
        "command": str(tmp_path / "no-such-runner"),
        "args": ["conscio-mcp", "--storage", str(space)]}}}), encoding="utf-8")
    os.environ["CONSCIO_CODEX_MCP_CONFIG"] = str(mcp)
    os.environ.pop("CONSCIO_CODEX_NO_INPROCESS", None)
    try:
        assert _hook()._codex_storage(None) == space
    finally:
        os.environ.pop("CONSCIO_CODEX_MCP_CONFIG", None)
        os.environ.pop("CONSCIO_CODEX_NO_INPROCESS", None)


def test_storage_mcp_interpreter_resolves_the_server_space(tmp_path):
    """(Claude 529-1) when the package is NOT importable in-process (forced
    off here), the hook runs the MCP server's OWN interpreter (from .mcp.json)
    with -c and reads the resolved path from stdout. The fake interpreter stands
    in for `uvx --from conscio==X python` and echoes the resolver's input."""
    space = _make_space(tmp_path)
    interp = tmp_path / "fake-uvx"
    interp.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "s = os.environ.get('CONSCIO_CODEX_STORAGE_INPUT') or ''\n"
        "sys.stdout.write(s)\n"
        "sys.exit(0 if s else 1)\n", encoding="utf-8")
    interp.chmod(0o755)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"conscio": {
        "command": str(interp),
        "args": ["conscio-mcp", "--storage", str(space)]}}}), encoding="utf-8")
    os.environ["CONSCIO_CODEX_MCP_CONFIG"] = str(mcp)
    os.environ["CONSCIO_CODEX_NO_INPROCESS"] = "1"      # force the interpreter path
    try:
        assert _hook()._codex_storage(None) == space
    finally:
        os.environ.pop("CONSCIO_CODEX_MCP_CONFIG", None)
        os.environ.pop("CONSCIO_CODEX_NO_INPROCESS", None)


def test_storage_broken_interpreter_logs_no_spawn_exit_zero(tmp_path):
    """(Claude 529-1) the .mcp.json interpreter points nowhere: the hook logs
    the failure, spawns no reactor, and still exits 0 (never traps the Codex
    turn)."""
    space = _make_space(tmp_path)
    sessions = tmp_path / "codex-sessions"
    sessions.mkdir()
    broken = tmp_path / "no-such-uvx"      # does not exist
    mcp = tmp_path / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"conscio": {
        "command": str(broken),
        "args": ["conscio-mcp", "--storage", str(space)]}}}), encoding="utf-8")
    env = _codex_env(tmp_path, sessions, None)
    env["CONSCIO_CODEX_MCP_CONFIG"] = str(mcp)
    env["CONSCIO_CODEX_NO_INPROCESS"] = "1"
    payload = {"session_id": SESSION_C, "transcript_path": str(sessions / "t.json")}
    r = subprocess.run(
        [sys.executable, str(HOOK), "codex_stop"],      # no --storage: the MCP path
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "could not resolve the space" in r.stderr, r.stderr
    assert not (space / "reactors" / f"{SESSION_C}.log").exists()   # no spawn
