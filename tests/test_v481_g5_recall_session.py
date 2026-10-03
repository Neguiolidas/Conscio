"""Tests for Conscio v4.8.1 G5 (P1 fix): conscio_recall_observations session scoping."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conscio import obsstore
from conscio.engine import ConsciousnessEngine
from conscio.mcp import jsonrpc as j
from conscio.mcp.schemas import BASE_TOOL_DEFS
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings

HOOKS_DIR = (
    Path(__file__).resolve().parent.parent
    / "conscio" / "integrations" / "claude_code" / "assets" / "hooks"
)


def _run_post_tool_use_hook(storage, payload):
    """Run the real PostToolUse hook the way the plugin's hooks.json invokes it."""
    # Outside the plugin data roots the hook writes to --storage as given;
    # inherited roots could redirect it to the live plugin space.
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_PLUGIN_DATA", "ZCODE_PLUGIN_DATA")}
    proc = subprocess.run(
        [sys.executable, str(HOOKS_DIR / "conscio_deepminer.py"), "post-tool-use",
         "--obsstore", str(HOOKS_DIR / "conscio_obsstore.py"), "--storage", str(storage)],
        input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


def test_schema_exposes_session_id():
    tool_def = next(t for t in BASE_TOOL_DEFS if t["name"] == "conscio_recall_observations")
    props = tool_def["inputSchema"]["properties"]
    assert "session_id" in props
    assert props["session_id"]["type"] == "string"


def test_obsstore_latest_session_for_project(tmp_path):
    db_file = tmp_path / "obs.db"
    conn = obsstore.connect(db_file)

    assert obsstore.latest_session_for_project(conn, "proj-a") == ""

    # Put observations in proj-a
    obsstore.put_observation(conn, tool="t1", input_text="i1", output_text="o1",
                             project="proj-a", agent="test", session_id="sess-1",
                             ts="2026-10-02T12:00:00")
    obsstore.put_observation(conn, tool="t2", input_text="i2", output_text="o2",
                             project="proj-b", agent="test", session_id="sess-b",
                             ts="2026-10-02T12:01:00")
    obsstore.put_observation(conn, tool="t3", input_text="i3", output_text="o3",
                             project="proj-a", agent="test", session_id="sess-2",
                             ts="2026-10-02T12:02:00")
    # Empty session_id should be ignored
    obsstore.put_observation(conn, tool="t4", input_text="i4", output_text="o4",
                             project="proj-a", agent="test", session_id="",
                             ts="2026-10-02T12:03:00")

    assert obsstore.latest_session_for_project(conn, "proj-a") == "sess-2"
    assert obsstore.latest_session_for_project(conn, "proj-b") == "sess-b"
    assert obsstore.latest_session_for_project(conn, "proj-c") == ""
    conn.close()


def test_engine_recall_observations_unwired_session_fails_guard(tmp_path):
    eng = ConsciousnessEngine(model_name="mock", storage_path=tmp_path, delivery_check=False)
    # Explicit empty session_id must NOT use random uuid.uuid4().hex to satisfy scope='session'
    with pytest.raises(ValueError, match="scope='session' requires a non-empty session_id"):
        eng.recall_observations("query", scope="session", session_id="")


def test_mcp_recall_observations_e2e_resolution(tmp_path, monkeypatch):
    space_dir = tmp_path / "space"
    space_dir.mkdir()
    eng = ConsciousnessEngine(model_name="mock", storage_path=space_dir, delivery_check=False)
    bindings = Bindings(eng, SeenStore(":memory:"))

    # Mock working directory to a known project
    fake_project = str(tmp_path / "my_project")
    monkeypatch.setattr(obsstore, "project_root", lambda cwd=None: fake_project)

    # 1. No observations in project -> InvalidParams
    with pytest.raises(j.InvalidParams, match="scope='session' requires a session_id"):
        bindings.call_tool("conscio_recall_observations", {"query": "anything"})

    # 2. Hook records an observation for fake_project under a specific session_id
    host_session = "host-session-xyz"
    eng.observe(tool="read_file", input_text="cat config.json",
                output_text="secret_config_value", project=fake_project,
                agent="claude-code", session_id=host_session)

    # 3. Recall with default scope (session) and no session_id -> finds it via latest_in_project
    res = bindings.call_tool("conscio_recall_observations", {"query": "secret_config_value"})
    payload = json.loads(res["content"][0]["text"])
    assert payload["session_id"] == host_session
    assert payload["session_source"] == "latest_in_project"
    assert len(payload["observations"]) == 1
    assert "secret_config_value" in payload["observations"][0]["output"]

    # 4. Recall with explicit session_id matching
    res_exp = bindings.call_tool("conscio_recall_observations", {
        "query": "secret_config_value",
        "session_id": host_session,
    })
    payload_exp = json.loads(res_exp["content"][0]["text"])
    assert payload_exp["session_id"] == host_session
    assert payload_exp["session_source"] == "explicit"
    assert len(payload_exp["observations"]) == 1

    # 5. Recall with explicit different session_id -> empty observations (honest miss)
    res_other = bindings.call_tool("conscio_recall_observations", {
        "query": "secret_config_value",
        "session_id": "other-session",
    })
    payload_other = json.loads(res_other["content"][0]["text"])
    assert payload_other["session_id"] == "other-session"
    assert payload_other["session_source"] == "explicit"
    assert len(payload_other["observations"]) == 0


def test_real_hook_write_is_found_by_default_mcp_recall(tmp_path, monkeypatch):
    """P1 end to end: the row the real hook writes is the row default recall reads.

    Nothing is mocked. The hook and the server each derive ``project`` on their
    own (the hook keeps a stdlib copy of the rule), so this proves the two agree
    when the tool ran in one subdirectory and the server sits in another.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    hook_cwd = repo / "pkg"
    hook_cwd.mkdir()
    server_cwd = repo / "docs" / "guides"
    server_cwd.mkdir(parents=True)
    space = tmp_path / "space"
    space.mkdir()

    host_session = "host-session-e2e"
    _run_post_tool_use_hook(space, {
        "hook_event_name": "PostToolUse",
        "session_id": host_session,
        "cwd": str(hook_cwd),
        "tool_name": "Bash",
        "tool_input": {"command": "echo marker"},
        "tool_response": {"stdout": "hook_written_marker", "stderr": ""},
    })

    monkeypatch.chdir(server_cwd)
    eng = ConsciousnessEngine(model_name="mock", storage_path=space, delivery_check=False)
    bindings = Bindings(eng, SeenStore(":memory:"))
    res = bindings.call_tool("conscio_recall_observations", {"query": "hook_written_marker"})
    payload = json.loads(res["content"][0]["text"])

    assert payload["session_id"] == host_session
    assert payload["session_source"] == "latest_in_project"
    assert len(payload["observations"]) == 1
    assert "hook_written_marker" in payload["observations"][0]["output"]
