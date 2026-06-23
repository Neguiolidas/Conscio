# tests/test_mcp_state_tools.py
from conscio.engine import ConsciousnessEngine
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings


def _bindings(tmp_path):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    seen = SeenStore(tmp_path / "mcp_seen.db")
    return Bindings(eng, seen, adapter_name=None, workspace_id="ws"), eng, seen


def test_state_tools_present_without_act(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        names = set(b._tools())
        assert {"conscio.state", "conscio.events", "conscio.handoff"} <= names
        assert "conscio.act" not in names               # still propose-only
        deflist = {d["name"] for d in b.tool_defs()}
        assert {"conscio.state", "conscio.events", "conscio.handoff"} <= deflist
    finally:
        seen.close()
        eng.close()


def test_state_tool_matches_state_resource(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        tool_val = b._tools()["conscio.state"]({})
        import json as _j
        res = b.read_resource("conscio://state")
        res_val = _j.loads(res["contents"][0]["text"])
        assert tool_val == res_val
    finally:
        seen.close()
        eng.close()


def test_events_tool_returns_list(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        assert isinstance(b._tools()["conscio.events"]({"limit": 5}), list)
    finally:
        seen.close()
        eng.close()


def test_handoff_tool_returns_str(tmp_path):
    b, eng, seen = _bindings(tmp_path)
    try:
        assert isinstance(b._tools()["conscio.handoff"]({}), str)
    finally:
        seen.close()
        eng.close()
