"""Handshake capability + the tools/list_changed notification channel."""
import io
import json

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


def test_queued_notification_is_written_after_the_response(tmp_path):
    from conscio.mcp import server

    b, engine = _bindings(tmp_path)
    outstream = io.StringIO()
    try:
        b.enqueue_notification("notifications/tools/list_changed")
        server.serve(b, io.StringIO(json.dumps(INIT) + "\n"), outstream)
    finally:
        engine.close()

    frames = [json.loads(line)
              for line in outstream.getvalue().splitlines() if line.strip()]
    assert len(frames) == 2, frames
    assert frames[0].get("id") == 0                      # the response first
    assert frames[1] == {"jsonrpc": "2.0",
                         "method": "notifications/tools/list_changed"}


def test_switching_mode_emits_list_changed(tmp_path):
    from conscio.mcp import server

    b, engine = _bindings(tmp_path, mode="ultra")
    outstream = io.StringIO()
    try:
        call = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": "conscio.mode", "arguments": {"set": "lite"}}}
        # INIT primeiro: o Dispatcher nasce com initialized=False e recusa
        # tools/call antes do handshake.
        raw = json.dumps(INIT) + "\n" + json.dumps(call) + "\n"
        server.serve(b, io.StringIO(raw), outstream)
    finally:
        engine.close()

    frames = [json.loads(line)
              for line in outstream.getvalue().splitlines() if line.strip()]
    assert [f.get("id") for f in frames[:2]] == [0, 7]      # init, depois o call
    assert frames[2]["method"] == "notifications/tools/list_changed"


def test_setting_the_same_mode_emits_nothing(tmp_path):
    b, engine = _bindings(tmp_path, mode="lite")
    try:
        b.call_tool("conscio.mode", {"set": "lite"})
        assert b.drain_notifications() == []
    finally:
        engine.close()


def test_drain_empties_the_queue(tmp_path):
    b, engine = _bindings(tmp_path)
    try:
        b.enqueue_notification("notifications/tools/list_changed")
        assert len(b.drain_notifications()) == 1
        assert b.drain_notifications() == []
    finally:
        engine.close()
