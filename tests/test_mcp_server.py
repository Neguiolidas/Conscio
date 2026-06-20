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
        assert "conscio.feed" in names and "conscio.propose_action" in names
        assert "conscio.act" not in names and "conscio.register_tool" not in names
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
                              "params": {"name": "conscio.feed",
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
                "params": {"name": "conscio.feed", "arguments": {"event": ev}}}
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
                        "params": {"name": "conscio.note",
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
                              "params": {"name": "conscio.propose_action",
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
                              "params": {"name": "conscio.feed",
                                         "arguments": {"event": {"type": "x"}}}}])
        assert out[1]["error"]["code"] == jj.INVALID_PARAMS
    finally:
        seen.close()
        eng.close()
