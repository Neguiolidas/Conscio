# tests/test_mcp_protocol.py
from conscio.mcp import jsonrpc as j
from conscio.mcp.protocol import SUPPORTED_PROTOCOLS, Dispatcher


class FakeBindings:
    def version(self):
        return "2.0.0"

    def conscio_meta(self):
        return {"workspace_id": "ws", "awake": False, "act_enabled": False,
                "adapter": None, "supported_protocols": SUPPORTED_PROTOCOLS}

    def tool_defs(self):
        return [{"name": "conscio.advisory", "description": "x",
                 "inputSchema": {"type": "object"}}]

    def resource_defs(self):
        return [{"uri": "conscio://advisory", "name": "advisory",
                 "description": "x", "mimeType": "application/json"}]

    def call_tool(self, name, args):
        if name == "conscio.advisory":
            return {"content": [{"type": "text", "text": "{}"}]}
        raise j.MethodNotFound(f"no tool {name}")

    def read_resource(self, uri):
        return {"contents": [{"uri": uri, "mimeType": "application/json",
                              "text": "{}"}]}


def _d():
    return Dispatcher(FakeBindings())


def _init(d, version="2025-06-18"):
    return d.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                     "params": {"protocolVersion": version}})


def test_initialize_negotiates_supported_version():
    res = _init(_d(), "2025-03-26")["result"]
    assert res["protocolVersion"] == "2025-03-26"
    assert res["serverInfo"]["name"] == "conscio"
    assert res["conscio"]["workspace_id"] == "ws"


def test_initialize_falls_back_to_latest():
    assert _init(_d(), "1999-01-01")["result"]["protocolVersion"] == SUPPORTED_PROTOCOLS[-1]


def test_notification_returns_none():
    d = _d(); _init(d)
    assert d.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_after_init():
    d = _d(); _init(d)
    res = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert res["result"]["tools"][0]["name"] == "conscio.advisory"


def test_tools_call_routes():
    d = _d(); _init(d)
    res = d.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "conscio.advisory", "arguments": {}}})
    assert res["result"]["content"][0]["type"] == "text"


def test_unknown_method_is_method_not_found():
    d = _d(); _init(d)
    res = d.handle({"jsonrpc": "2.0", "id": 3, "method": "bogus"})
    assert res["error"]["code"] == j.METHOD_NOT_FOUND


def test_unavailable_tool_is_method_not_found():
    d = _d(); _init(d)
    res = d.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "conscio.act", "arguments": {}}})
    assert res["error"]["code"] == j.METHOD_NOT_FOUND


def test_request_before_initialize_is_invalid_request():
    res = _d().handle({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
    assert res["error"]["code"] == j.INVALID_REQUEST


def test_non_2_0_is_invalid_request():
    res = _d().handle({"jsonrpc": "1.0", "id": 5, "method": "ping"})
    assert res["error"]["code"] == j.INVALID_REQUEST


def test_ping_after_init_is_empty_result():
    d = _d(); _init(d)
    assert d.handle({"jsonrpc": "2.0", "id": 6, "method": "ping"})["result"] == {}
