"""Handshake capability + the tools/list_changed notification channel."""
from conscio.engine import ConsciousnessEngine
from conscio.mcp.protocol import Dispatcher
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"}}


def _bindings(tmp_path, **kw):
    engine = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    return Bindings(engine, SeenStore(tmp_path / "mcp_seen.db"), **kw), engine


def test_initialize_announces_list_changed(tmp_path):
    b, engine = _bindings(tmp_path)
    try:
        res = Dispatcher(b).handle(INIT)
    finally:
        engine.close()
    assert res["result"]["capabilities"]["tools"]["listChanged"] is True
