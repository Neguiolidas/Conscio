# tests/test_mcp_battery.py
"""v2.0 adversarial battery — transport + bindings survive hostile host input:
malformed/oversized frames, recovery, wrong protocol, before-init, no-adapter
fail-closed, duplicate-id prior-result, workspace isolation, act absent."""
import io
import json

from conscio.agency import MockAdapter
from conscio.engine import ConsciousnessEngine
from conscio.mcp import jsonrpc as j
from conscio.mcp.protocol import Dispatcher
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings, serve

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"}}


def _bindings(tmp_path, ws="ws"):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    seen = SeenStore(tmp_path / "mcp_seen.db")
    return Bindings(eng, seen, workspace_id=ws), eng, seen


def _run(b, raw, *, max_bytes=j.DEFAULT_MAX_FRAME_BYTES):
    out = io.StringIO()
    serve(b, io.StringIO("".join(raw)), out, max_bytes=max_bytes)
    return [json.loads(x) for x in out.getvalue().splitlines() if x]


def test_try_break_garbage_is_parse_error(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        assert _run(b, ["{not json\n"])[0]["error"]["code"] == j.PARSE_ERROR
    finally:
        seen.close()
        eng.close()


def test_try_break_oversized_frame_no_oom(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        big = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping",
                          "params": {"x": "a" * 10_000}}) + "\n"
        assert _run(b, [big], max_bytes=500)[0]["error"]["code"] == j.INVALID_REQUEST
    finally:
        seen.close()
        eng.close()


def test_try_break_recovers_after_bad_frame(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        out = _run(b, ["garbage\n", json.dumps(INIT) + "\n",
                       json.dumps({"jsonrpc": "2.0", "id": 1,
                                   "method": "ping"}) + "\n"])
        assert out[0]["error"]["code"] == j.PARSE_ERROR
        assert out[1]["result"]["serverInfo"]["name"] == "conscio"
        assert out[2]["result"] == {}
    finally:
        seen.close()
        eng.close()


def test_try_break_oversized_then_recovers(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        out = _run(b, [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping",
                                   "params": {"x": "a" * 5000}}) + "\n",
                       json.dumps(INIT) + "\n"], max_bytes=400)
        assert out[0]["error"]["code"] == j.INVALID_REQUEST
        assert out[1]["result"]["serverInfo"]["name"] == "conscio"
    finally:
        seen.close()
        eng.close()


def test_try_break_act_tool_absent(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        out = _run(b, [json.dumps(INIT) + "\n",
                       json.dumps({"jsonrpc": "2.0", "id": 1,
                                   "method": "tools/call",
                                   "params": {"name": "conscio.act",
                                              "arguments": {}}}) + "\n"])
        assert out[1]["error"]["code"] == j.METHOD_NOT_FOUND
        list_out = _run(b, [json.dumps(INIT) + "\n",
                            json.dumps({"jsonrpc": "2.0", "id": 2,
                                        "method": "tools/list"}) + "\n"])
        assert "conscio.act" not in {t["name"]
                                     for t in list_out[1]["result"]["tools"]}
    finally:
        seen.close()
        eng.close()


def test_try_break_propose_without_adapter_fails_closed(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        out = _run(b, [json.dumps(INIT) + "\n",
                       json.dumps({"jsonrpc": "2.0", "id": 1,
                                   "method": "tools/call",
                                   "params": {"name": "conscio.propose_action",
                                              "arguments": {"intent": {
                                                  "tool": "t", "args": {},
                                                  "rationale": "r",
                                                  "expected_outcome": "o"}}}}) + "\n"])
        body = json.loads(out[1]["result"]["content"][0]["text"])
        assert body["verdict"] == "FAIL" and "no adapter" in body["reasons"][0]
    finally:
        seen.close()
        eng.close()


def test_try_break_dup_event_no_world_inflation(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        ev = {"id": "same", "type": "perception", "source": "h",
              "category": "h", "payload": {"x": 1}}
        call = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "conscio.note",
                                      "arguments": {"event": ev}}}) + "\n"
        _run(b, [json.dumps(INIT) + "\n", call, call])
        assert seen.conn.execute("SELECT COUNT(*) FROM mcp_seen").fetchone()[0] == 1
    finally:
        seen.close()
        eng.close()


def test_try_break_two_workspaces_isolated(tmp_path):
    a, ea, sa = _bindings(tmp_path / "A", ws="wsA")
    b, eb, sb = _bindings(tmp_path / "B", ws="wsB")
    try:
        ra = _run(a, [json.dumps(INIT) + "\n"])
        rb = _run(b, [json.dumps(INIT) + "\n"])
        assert ra[0]["result"]["conscio"]["workspace_id"] == "wsA"
        assert rb[0]["result"]["conscio"]["workspace_id"] == "wsB"
        assert (tmp_path / "A" / "mcp_seen.db").exists()
        assert (tmp_path / "B" / "mcp_seen.db").exists()
    finally:
        sa.close()
        ea.close()
        sb.close()
        eb.close()


# ── v2.0.1 Full Act wiring (Task 11): act tools + namespaced idempotency ──

class _PassSkeptic:
    """Deterministic PASS — the MCP wiring/idempotency is under test here, not
    the Skeptic (unit-tested separately with the engine's real adapter)."""

    def audit(self, proposal, *, goal_text=""):
        from conscio.agency.contracts import AuditVerdict
        return AuditVerdict(verdict="PASS", reasons=[], risk_flags=[],
                            confidence=0.9)


_MA = [{"name": "deploy", "params": {"env": {"type": "str", "required": True}},
        "risk": "low", "approval_policy": "auto"}]


def _wired(tmp_path):
    e = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    e.attach_adapter(MockAdapter(script=[]))
    e.wake()
    seen = SeenStore(tmp_path / "mcp_seen.db")
    b = Bindings(e, seen, adapter_name="mock", workspace_id="ws", act_flag=True)
    d = Dispatcher(b)
    d.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"conscio": {"tools": _MA}}})
    e.host_act.skeptic = _PassSkeptic()         # deterministic verdict
    return d, b, e, seen


def _call(d, name, args, _id=1):
    res = d.handle({"jsonrpc": "2.0", "id": _id, "method": "tools/call",
                    "params": {"name": name, "arguments": args}})
    return json.loads(res["result"]["content"][0]["text"])


def test_act_executable_then_report(tmp_path):
    d, b, e, s = _wired(tmp_path)
    try:
        out = _call(d, "conscio.act", {"intent": {
            "tool": "deploy", "args": {"env": "prod"},
            "rationale": "r", "expected_outcome": "o"}})
        assert out["status"] == "executable"
        rep = _call(d, "conscio.report_result",
                    {"ledger_id": out["ledger_id"], "result": {"ok": True}})
        assert rep["status"] == "executed"
    finally:
        s.close()
        e.close()


def test_act_idempotency_key_returns_prior(tmp_path):
    d, b, e, s = _wired(tmp_path)
    try:
        intent = {"tool": "deploy", "args": {"env": "prod"}, "rationale": "r",
                  "expected_outcome": "o", "idempotency_key": "k1"}
        a = _call(d, "conscio.act", {"intent": intent})
        bb = _call(d, "conscio.act", {"intent": intent})
        assert a == bb                              # exact prior, same ledger_id
    finally:
        s.close()
        e.close()


def test_act_absent_when_disabled_is_method_not_found(tmp_path):
    e = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    e.attach_adapter(MockAdapter(script=[]))
    seen = SeenStore(tmp_path / "mcp_seen.db")
    d = Dispatcher(Bindings(e, seen, act_flag=False))
    try:
        d.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                  "params": {}})
        res = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "conscio.act", "arguments": {}}})
        assert res["error"]["code"] == j.METHOD_NOT_FOUND
    finally:
        seen.close()
        e.close()


def test_act_tools_reject_bad_typed_args(tmp_path):
    d, b, e, s = _wired(tmp_path)
    try:
        def _err(name, args):
            return d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": name, "arguments": args}}
                            )["error"]["code"]
        assert _err("conscio.approve", {"ledger_id": "abc"}) == j.INVALID_PARAMS
        assert _err("conscio.pending", {"limit": "abc"}) == j.INVALID_PARAMS
        assert _err("conscio.report_result",
                    {"ledger_id": 1, "result": "nope"}) == j.INVALID_PARAMS
    finally:
        s.close()
        e.close()
