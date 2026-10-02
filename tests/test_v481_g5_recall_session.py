"""Tests for Conscio v4.8.1 G5 (P1 fix): conscio_recall_observations session scoping."""
import json

import pytest

from conscio import obsstore
from conscio.engine import ConsciousnessEngine
from conscio.mcp import jsonrpc as j
from conscio.mcp.schemas import BASE_TOOL_DEFS
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings


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
