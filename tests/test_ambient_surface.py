# tests/test_ambient_surface.py
import json

from conscio.ambient import board, cli, paths, surface
from conscio.engine import ConsciousnessEngine
from conscio.mcp.schemas import BOARD_DISPATCH_DEF
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings


def _bind(tmp_path, *, instance_id, relay):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "space")
    seen = SeenStore(tmp_path / "space" / "mcp_seen.db")
    b = Bindings(eng, seen, adapter_name=None, workspace_id="ws",
                 self_instance_id=instance_id, liaison_db=tmp_path / "liaison.db",
                 relay=relay, hermes_review=False)
    return b, eng


def _call(b, args):
    return json.loads(b.call_tool("conscio_board", args)["content"][0]["text"])


def test_board_tool_advertised_only_with_relay(tmp_path):
    b, eng = _bind(tmp_path, instance_id="S", relay=False)
    try:
        assert "conscio_board" not in {d["name"] for d in b.tool_defs()}
    finally:
        eng.close()
    b, eng = _bind(tmp_path, instance_id="S", relay=True)
    try:
        assert "conscio_board" in {d["name"] for d in b.tool_defs()}
    finally:
        eng.close()


def test_board_schema_ops_match_surface():
    assert BOARD_DISPATCH_DEF["inputSchema"]["properties"]["op"]["enum"] == list(surface.OPS)


def test_surface_actor_is_the_server_identity(tmp_path):
    b, eng = _bind(tmp_path, instance_id="S", relay=True)
    try:
        out = _call(b, {"op": "propose", "title": "t", "actor": "EVIL",
                        "creator": "EVIL", "as": "EVIL"})
        assert out["ok"] is True
        shown = _call(b, {"op": "show", "task_id": out["task_id"]})
        assert shown["task"]["creator"] == "S"
    finally:
        eng.close()


def test_surface_keeps_orch_fence_in_space(tmp_path):
    space_o, space_b = tmp_path / "o", tmp_path / "b"
    got = surface.run_op({"op": "orchestrate", "action": "acquire"}, actor="O", space=space_o)
    assert got == {"ok": True, "orch_fence": 1}
    assert surface.load_orch_fence(space_o, paths.board_path()) == 1
    made = surface.run_op({"op": "create", "title": "t", "assignee": "A"},
                          actor="O", space=space_o)
    assert made["ok"] is True
    refused = surface.run_op({"op": "create", "title": "t", "assignee": "A"},
                             actor="B", space=space_b)
    assert refused == {"ok": False,
                       "error": "StaleFence (orchestration, fence 0, current 1)"}


def test_surface_errors_are_product_text_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CONSCIO_SELF_ID", "X")
    rc = cli.main(["--storage", str(tmp_path / "space"), "task", "show", "99"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err == "NoSuchTask (task 99)\n"
    assert "Traceback" not in captured.out + captured.err


def test_conscio_ambient_is_wired_in_the_main_cli(tmp_path, monkeypatch, capsys):
    from conscio import cli as main_cli
    monkeypatch.setenv("CONSCIO_SELF_ID", "X")
    assert main_cli.main(["ambient", "--storage", str(tmp_path / "space"), "status"]) == 0
    assert json.loads(capsys.readouterr().out)["status"]["schema"] == 1


def test_cli_claim_then_submit_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CONSCIO_SELF_ID", "A")
    space = str(tmp_path / "space")
    surface.run_op({"op": "orchestrate", "action": "acquire"}, actor="A", space=tmp_path / "space")
    surface.run_op({"op": "create", "title": "t", "assignee": "A"}, actor="A",
                   space=tmp_path / "space")
    assert cli.main(["--storage", space, "task", "claim", "1"]) == 0
    fence = json.loads(capsys.readouterr().out)["fence"]
    assert cli.main(["--storage", space, "task", "submit", "1", "--fence", str(fence)]) == 0
    db = board.open_board(paths.board_path())
    try:
        assert board._task(db, 1)["state"] == "done"
    finally:
        db.close()
