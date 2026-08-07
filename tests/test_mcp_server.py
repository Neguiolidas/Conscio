# tests/test_mcp_server.py
import io
import json

from conscio.agency import MockAdapter
from conscio.engine import ConsciousnessEngine
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings, serve

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"}}


def _bindings(tmp_path, *, script=None, ws="ws123"):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    if script is not None:
        eng.attach_adapter(MockAdapter(script=script))
    seen = SeenStore(tmp_path / "mcp_seen.db")
    return Bindings(eng, seen, adapter_name="mock" if script else None,
                    workspace_id=ws), eng, seen


def _run(bindings, requests):
    out = io.StringIO()
    serve(bindings, io.StringIO("".join(json.dumps(r) + "\n" for r in requests)),
          out)
    return [json.loads(x) for x in out.getvalue().splitlines() if x]


def test_initialize_then_tools_list_is_propose_only(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        out = _run(b, [INIT, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
        names = {t["name"] for t in out[1]["result"]["tools"]}
        assert "conscio_feed" in names and "conscio_propose_action" in names
        assert "conscio_act" not in names and "conscio.register_tool" not in names
        assert out[0]["result"]["conscio"]["act_enabled"] is False
    finally:
        seen.close()
        eng.close()


def test_feed_ingests_and_returns_advisory(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        ev = {"id": "e1", "type": "perception", "source": "host",
              "category": "host", "payload": {"cpu": 0.4}}
        out = _run(b, [INIT, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "conscio_feed",
                                         "arguments": {"event": ev}}}])
        body = json.loads(out[1]["result"]["content"][0]["text"])
        assert body["event_id"] == "e1" and "advisory" in body
    finally:
        seen.close()
        eng.close()


def test_feed_duplicate_returns_identical_prior_result(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        ev = {"id": "dup", "type": "perception", "source": "h",
              "category": "h", "payload": {"x": 1}}
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "conscio_feed", "arguments": {"event": ev}}}
        out = _run(b, [INIT, call, call])
        first = json.loads(out[1]["result"]["content"][0]["text"])
        second = json.loads(out[2]["result"]["content"][0]["text"])
        assert first == second                 # exact prior result, not {deduped}
    finally:
        seen.close()
        eng.close()


def test_note_maps_host_type_to_valid_category(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        ev = {"id": "n1", "type": "user_msg", "source": "alice",
              "category": "user", "payload": {"text": "hi"}}
        _run(b, [INIT, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "conscio_note",
                                   "arguments": {"event": ev}}}])
        rows = eng.event_bus.query(type="host:event", limit=5)
        assert rows and rows[0].data["host_type"] == "user_msg"
    finally:
        seen.close()
        eng.close()


def test_read_resource_advisory(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        out = _run(b, [INIT, {"jsonrpc": "2.0", "id": 1,
                              "method": "resources/read",
                              "params": {"uri": "conscio://advisory"}}])
        assert isinstance(json.loads(out[1]["result"]["contents"][0]["text"]), dict)
    finally:
        seen.close()
        eng.close()


def test_read_events_resource_honours_query(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        eng.event_bus.emit(type="host:event", category="external",
                           data={"host_type": "x"})
        out = _run(b, [INIT, {"jsonrpc": "2.0", "id": 1,
                              "method": "resources/read",
                              "params": {"uri": "conscio://events?type=host:event&limit=10"}}])
        rows = json.loads(out[1]["result"]["contents"][0]["text"])
        assert all(r["type"] == "host:event" for r in rows)
    finally:
        seen.close()
        eng.close()


def test_propose_action_over_mcp(tmp_path):
    b, eng, seen = _bindings(tmp_path, script=["A1: NO\nA2: NO\nA3: YES"])
    try:
        intent = {"tool": "read_file", "args": {"path": "x"},
                  "rationale": "inspect", "expected_outcome": "contents"}
        out = _run(b, [INIT, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "conscio_propose_action",
                                         "arguments": {"intent": intent}}}])
        assert json.loads(out[1]["result"]["content"][0]["text"])["verdict"] == "PASS"
    finally:
        seen.close()
        eng.close()


def test_invalid_event_is_invalid_params(tmp_path):
    from conscio.mcp import jsonrpc as jj
    b, eng, seen = _bindings(tmp_path)
    try:
        out = _run(b, [INIT, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "conscio_feed",
                                         "arguments": {"event": {"type": "x"}}}}])
        assert out[1]["error"]["code"] == jj.INVALID_PARAMS
    finally:
        seen.close()
        eng.close()


# ── v2.0.1 Full Act (Task 10): dynamic tool visibility + conscio_meta ──

_MS = [{"name": "deploy", "params": {"env": {"type": "str", "required": True}},
        "risk": "low", "approval_policy": "auto"}]


def _act_bind(tmp_path, *, act_flag):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    eng.attach_adapter(MockAdapter(script=[]))
    seen = SeenStore(tmp_path / "mcp_seen.db")
    b = Bindings(eng, seen, adapter_name="mock", workspace_id="ws",
                 act_flag=act_flag)
    return b, eng, seen


def test_act_tools_absent_without_flag(tmp_path):
    b, eng, seen = _act_bind(tmp_path, act_flag=False)
    try:
        names = {t["name"] for t in b.tool_defs()}
        assert "conscio_act" not in names
        assert b.conscio_meta()["act_enabled"] is False
    finally:
        seen.close()
        eng.close()


def test_act_tools_present_after_enable(tmp_path):
    b, eng, seen = _act_bind(tmp_path, act_flag=True)
    try:
        b.on_initialize({"conscio": {"tools": _MS}})
        names = {t["name"] for t in b.tool_defs()}
        assert {"conscio_act", "conscio_report_result", "conscio_pending",
                "conscio_approve", "conscio_reject"} <= names
        meta = b.conscio_meta()
        assert meta["act_enabled"] is True and meta["host_tools_count"] == 1
        assert meta["adapter_ready"] is True and meta["manifest_hash"]
    finally:
        seen.close()
        eng.close()


def test_invalid_manifest_keeps_act_disabled(tmp_path):
    b, eng, seen = _act_bind(tmp_path, act_flag=True)
    try:
        b.on_initialize({"conscio": {"tools": [{"risk": "boom"}]}})
        assert "conscio_act" not in {t["name"] for t in b.tool_defs()}
        meta = b.conscio_meta()
        assert meta["act_enabled"] is False and meta["act_error"]
    finally:
        seen.close()
        eng.close()


def test_legacy_dotted_tool_name_still_dispatches(tmp_path):
    """v4.1 renamed the surface; a caller scripted against the old spelling must
    not break.

    The rename is invisible to a model (hosts sanitize the name they show it),
    but not to anything that hardcoded ``conscio.recall`` — a shell script, a
    peer agent, another host's config. Those get an alias, and only on the way
    in: nothing advertises a dotted name any more.
    """
    import pytest

    from conscio.mcp import jsonrpc

    b, eng, _ = _bindings(tmp_path)
    try:
        old = json.loads(b.call_tool("conscio.advisory", {})["content"][0]["text"])
        new = json.loads(b.call_tool("conscio_advisory", {})["content"][0]["text"])
        assert old.keys() == new.keys(), "alias reached a different handler"

        # o alias traduz, não adivinha: um nome inexistente ainda dói
        with pytest.raises(jsonrpc.MethodNotFound):
            b.call_tool("conscio.no_such_tool", {})

        assert not [d for d in b.tool_defs() if "." in d["name"]], \
            "a dotted name is being advertised again"
    finally:
        eng.close()
