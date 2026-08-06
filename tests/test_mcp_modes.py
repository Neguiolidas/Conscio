"""Tool-surface modes: nested sets, persistence, precedence."""
import pytest

from conscio.agency import MockAdapter
from conscio.engine import ConsciousnessEngine
from conscio.mcp import modes, schemas, server
from conscio.mcp.seen import SeenStore


def _bindings(tmp_path, **kw):
    engine = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    return server.Bindings(engine, SeenStore(tmp_path / "mcp_seen.db"), **kw), engine


def _names(mode, tmp_path):
    b, engine = _bindings(tmp_path, mode=mode)
    try:
        return {d["name"] for d in b.tool_defs()}
    finally:
        engine.close()


def test_sets_are_nested():
    assert modes.LITE_TOOLS < modes.BALANCED_TOOLS


def test_exact_counts(tmp_path):
    assert len(_names("lite", tmp_path)) == 10
    assert len(_names("balanced", tmp_path)) == 18
    assert len(_names("ultra", tmp_path)) == 35


def test_remember_is_present_in_every_mode(tmp_path):
    for mode in modes.MODES:
        assert "conscio.remember" in _names(mode, tmp_path), f"no memory write in {mode}"


def test_mode_tool_exists_in_every_mode(tmp_path):
    for mode in modes.MODES:
        assert "conscio.mode" in _names(mode, tmp_path), f"no way out of {mode}"


def test_mode_read_reports_current_surface(tmp_path):
    import json
    b, engine = _bindings(tmp_path, mode="balanced")
    try:
        out = json.loads(b.call_tool("conscio.mode", {})["content"][0]["text"])
        assert out["mode"] == "balanced"
        assert out["tools"] == 18
        assert out["modes"] == list(modes.MODES)
    finally:
        engine.close()


def test_mode_write_switches_and_persists(tmp_path):
    import json
    b, engine = _bindings(tmp_path, mode="ultra")
    try:
        out = json.loads(
            b.call_tool("conscio.mode", {"set": "lite"})["content"][0]["text"])
        assert out["mode"] == "lite"
        assert out["tools"] == 10
        assert len(b.tool_defs()) == 10
    finally:
        engine.close()
    assert modes.read_mode(tmp_path) == "lite"


def test_mode_write_rejects_unknown(tmp_path):
    from conscio.mcp import jsonrpc

    b, engine = _bindings(tmp_path, mode="ultra")
    try:
        # InvalidParams, não Exception: um AttributeError no handler passaria
        # por um raises(Exception) e o erro fantasma iria pro cliente como -32603.
        with pytest.raises(jsonrpc.InvalidParams):
            b.call_tool("conscio.mode", {"set": "turbo"})
        assert b.mode == "ultra"                 # recusa não move a superfície
        assert b.drain_notifications() == []     # nem avisa o host
    finally:
        engine.close()
    assert modes.read_mode(tmp_path) is None     # nem toca no disco


def test_default_mode_is_ultra_so_existing_installs_do_not_shrink():
    assert modes.DEFAULT_MODE == "ultra"


def test_lite_schemas_are_flat(tmp_path):
    b, engine = _bindings(tmp_path, mode="lite")
    try:
        for d in b.tool_defs():
            assert len(d["description"]) <= 120
            for prop in d["inputSchema"]["properties"].values():
                assert set(prop) == {"type"}, prop
    finally:
        engine.close()


def test_write_then_read_round_trip(tmp_path):
    modes.write_mode(tmp_path, "lite")
    assert modes.read_mode(tmp_path) == "lite"


def test_lite_does_not_void_an_explicit_flag(tmp_path):
    """A flag typed on the command line survives the mode filter (but gets flattened).

    Regressão da v3.9.9: `--enable-act --lite` expunha 8 ferramentas e engolia o
    `conscio.act` sem erro. Verificado no baseline antes de escrever este teste.
    `act_flag=True` sozinho não basta — `_act_enabled()` também exige `host_act`,
    então o manifesto precisa ser ligado de verdade, senão o teste passa por
    motivo errado (as ACT tools nunca teriam entrado).
    """
    engine = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    engine.attach_adapter(MockAdapter(script=[]))
    assert engine.enable_host_act(
        [{"name": "deploy", "description": "d",
          "input_schema": {"type": "object", "properties": {}},
          "risk": "low", "approval_policy": "auto"}]), "manifesto rejeitado"
    b = server.Bindings(engine, SeenStore(tmp_path / "mcp_seen.db"),
                        mode="lite", act_flag=True)
    try:
        assert b._act_enabled(), "guarda do teste: act precisa estar realmente ligado"
        defs = b.tool_defs()
    finally:
        engine.close()
    names = {d["name"] for d in defs}
    assert "conscio.act" in names, "explicit --enable-act was silently dropped by lite"
    assert len(names) == 10 + len(schemas.ACT_TOOL_DEFS)
    act = next(d for d in defs if d["name"] == "conscio.act")
    assert len(act["description"]) <= 120           # formatting still applies
    for prop in act["inputSchema"]["properties"].values():
        assert set(prop) == {"type"}, prop


def test_write_rejects_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        modes.write_mode(tmp_path, "turbo")


def test_read_returns_none_on_garbage(tmp_path):
    modes.mode_path(tmp_path).write_text("turbo")
    assert modes.read_mode(tmp_path) is None


def test_read_returns_none_when_absent(tmp_path):
    assert modes.read_mode(tmp_path) is None


def test_persisted_mode_beats_cli(tmp_path):
    modes.write_mode(tmp_path, "lite")
    assert modes.resolve_mode(tmp_path, "ultra") == "lite"


def test_cli_beats_default_when_nothing_persisted(tmp_path):
    assert modes.resolve_mode(tmp_path, "balanced") == "balanced"


def test_default_when_neither(tmp_path):
    assert modes.resolve_mode(tmp_path, None) == "ultra"
