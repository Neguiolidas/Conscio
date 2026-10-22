"""T2: the Codex hooks (Codex plan §3.3, amended §6) — host detection, the
SessionStart/Stop subcommands, the owner-pid walk-up and the JSON instruction
line. The reactor core is T1 (test_codex_reactor.py); here we test the hook
surface that arms it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
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


# ── T2b: the hook stays in-process only and hands the .mcp.json to the reactor ─

def test_spawn_spec_inprocess_present_uses_storage(tmp_path):
    """(T2b) when the in-process resolver lands (the package is importable in
    this python), the hook spawns the reactor with a resolved --storage and no
    --mcp-config. This is the cheap fast path."""
    space = _make_space(tmp_path)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"conscio": {
        "command": str(tmp_path / "no-such-runner"),
        "args": ["conscio-mcp", "--storage", str(space)]}}}), encoding="utf-8")
    os.environ["CONSCIO_CODEX_MCP_CONFIG"] = str(mcp)
    os.environ.pop("CONSCIO_CODEX_NO_INPROCESS", None)
    try:
        assert _hook()._codex_spawn_spec(None) == (space, None)
    finally:
        os.environ.pop("CONSCIO_CODEX_MCP_CONFIG", None)
        os.environ.pop("CONSCIO_CODEX_NO_INPROCESS", None)


def test_session_start_inprocess_absent_does_not_run_interpreter(tmp_path):
    """(Claude 538-1) with the in-process resolver absent, the hook must NOT run
    the server interpreter (uvx): a bomb interpreter records its invocation, and
    the hook never triggers it -- it exits in <1s with the JSON instruction,
    ready to hand the .mcp.json path to the reactor instead."""
    space = _make_space(tmp_path)
    sessions = tmp_path / "codex-sessions"
    sessions.mkdir()
    bomb = tmp_path / "bomb"
    bomb.write_text("#!/bin/sh\ntouch " + str(tmp_path / "bomb-ran") + "\n",
                    encoding="utf-8")
    bomb.chmod(0o755)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"conscio": {
        "command": str(bomb),
        "args": ["conscio-mcp", "--storage", str(space)]}}}), encoding="utf-8")
    env = _codex_env(tmp_path, sessions, None)
    env["CONSCIO_CODEX_MCP_CONFIG"] = str(mcp)
    env["CONSCIO_CODEX_NO_INPROCESS"] = "1"      # in-process resolver absent
    payload = {"session_id": SESSION_C, "transcript_path": str(sessions / "t.json")}
    start = time.time()
    r = subprocess.run(
        [sys.executable, str(HOOK), "codex_session_start"],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30)
    elapsed = time.time() - start
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = json.loads(r.stdout.strip())
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert elapsed < 1.0, f"the hook ran the (slow) interpreter: {elapsed:.2f}s"
    assert not (tmp_path / "bomb-ran").exists(), \
        "the hook invoked the server interpreter (T2b forbids that in the hook)"


def test_spawn_spec_inprocess_absent_hands_mcp_config(tmp_path):
    """(T2b) when the in-process resolver is absent and the harness --storage is
    a root-level path (the empty CLAUDE_PLUGIN_DATA case), the hook hands the
    .mcp.json path to the reactor (--mcp-config) rather than a resolved space."""
    space = _make_space(tmp_path)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"conscio": {
        "command": str(tmp_path / "no-such-runner"),
        "args": ["conscio-mcp", "--storage", str(space)]}}}), encoding="utf-8")
    os.environ["CONSCIO_CODEX_MCP_CONFIG"] = str(mcp)
    os.environ["CONSCIO_CODEX_NO_INPROCESS"] = "1"
    try:
        # /space is the empty-plugin-data root the Codex harness passes
        storage, mcp_config = _hook()._codex_spawn_spec("/space")
        assert storage is None, "in-process absent -> no resolved storage"
        assert mcp_config == str(mcp)
    finally:
        os.environ.pop("CONSCIO_CODEX_MCP_CONFIG", None)
        os.environ.pop("CONSCIO_CODEX_NO_INPROCESS", None)


# ── C1 (4.10.0): the Stop guard routes a Codex host to the reactor ───────────

HOOKS_JSON = HOOK.parent / "hooks.json"


def _claude_env(tmp_path: Path, space: Path) -> dict:
    """The env of the Claude Stop hook: the agent's space, its own spool (so
    the guard is on the relay) and a relay root under the test tree."""
    env = dict(os.environ)
    env["CONSCIO_RELAY_ROOT"] = str(tmp_path / "relay-root")
    (tmp_path / "relay-root" / "spool" / SELF_ID).mkdir(parents=True,
                                                        exist_ok=True)
    env["CONSCIO_SELF_ID"] = SELF_ID
    env.pop("CONSCIO_CODEX_SESSIONS", None)
    env.pop("CONSCIO_CODEX_BIN", None)
    return env


def test_stop_with_a_codex_payload_never_asks_for_a_waiter_and_wakes_the_reactor(
        tmp_path):
    """(C1) the shipped Stop command is the same for both hosts: the payload
    says Codex, so the guard hands the session to the reactor (exit 0, no
    'no waiter' message) instead of arming Claude's waiter (exit 2)."""
    space = _make_space(tmp_path)
    sessions = tmp_path / "codex-sessions"
    sessions.mkdir()
    # the codex ancestor: a copy of bash named 'codex' (the owner walk looks
    # for an ancestor whose argv[0] is the codex binary)
    fake_codex = tmp_path / "codex"
    fake_codex.write_bytes(Path("/bin/bash").read_bytes())
    fake_codex.chmod(0o755)
    env = _claude_env(tmp_path, space)
    env["CONSCIO_CODEX_SESSIONS"] = str(sessions)
    env["CONSCIO_CODEX_BIN"] = str(fake_codex)
    payload = {"session_id": SESSION_C, "transcript_path": str(sessions / "t.json")}
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps(payload), encoding="utf-8")
    hook_cmd = f"{sys.executable} {HOOK} stop --storage {space}"
    # the '&& true' keeps bash from exec'ing the hook in place: the codex
    # ancestor has to stay alive as the hook's parent
    r = subprocess.run(
        ["bash", "-c",
         (f"cat {payload_file} | {fake_codex} -c "
          f"{shlex.quote(hook_cmd + ' && true')}")],
        capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "no waiter" not in r.stderr, r.stderr
    log = space / "reactors" / f"{SESSION_C}.log"
    deadline = time.time() + 10
    while time.time() < deadline and not log.exists():
        time.sleep(0.05)
    assert log.exists(), "the reactor was not triggered"


def test_stop_with_a_claude_payload_still_asks_for_the_waiter(tmp_path):
    """(C1) the Claude behaviour is untouched: a Claude payload without a
    waiter still exits 2 with the arming command on stderr."""
    space = _make_space(tmp_path)
    env = _claude_env(tmp_path, space)
    payload = {"session_id": SESSION_C, "transcript_path": "/not/a/codex/t.json"}
    r = subprocess.run(
        [sys.executable, str(HOOK), "stop", "--storage", str(space)],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
        timeout=30)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "no waiter for this session" in r.stderr, r.stderr


def test_the_shipped_hooks_json_arms_the_reactor_without_machine_paths():
    """(C1) the shipped hooks.json: the SessionStart arms the Codex reactor,
    every command is relative to ${CLAUDE_PLUGIN_ROOT} (no machine path), and
    the Stop keeps calling `stop` -- the code, not the file, routes a Codex
    payload."""
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    hooks = data["hooks"]
    commands = [h["command"] for group in hooks.values()
                for entry in group for h in entry["hooks"]]
    start = [h["command"] for entry in hooks["SessionStart"]
             for h in entry["hooks"]]
    assert any("codex_session_start" in c for c in start), start
    stop = [h["command"] for entry in hooks["Stop"] for h in entry["hooks"]]
    assert any('conscio_wake.py" stop --storage "${CLAUDE_PLUGIN_DATA}/space"'
               in c for c in stop), stop
    for c in commands:
        assert "${CLAUDE_PLUGIN_ROOT}" in c, c
        assert "/home/" not in c, c
        assert c.startswith('python3 "${CLAUDE_PLUGIN_ROOT}/'), c
