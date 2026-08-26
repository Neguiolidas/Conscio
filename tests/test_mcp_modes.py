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
    assert len(_names("ultra", tmp_path)) == 37


def test_description_counts_match_the_served_surface(tmp_path):
    """The counts in the description are served to every host, so they are
    load-bearing prose — and nothing was measuring them.

    ``test_exact_counts`` pins the surface, but it cannot catch this drift: add
    a tool, update that test to 36, and the description still advertises 35 to
    every caller with the suite green.
    """
    import re

    description = schemas.MODE_TOOL_DEF["description"]
    for mode in modes.MODES:
        # matches both "lite (10 tools)" and "ultra (all 35)"
        stated = re.search(rf"{mode}\s*\((?:all\s+)?(\d+)", description)
        assert stated, f"description states no tool count for {mode}: {description!r}"
        served = len(_names(mode, tmp_path))
        assert int(stated.group(1)) == served, (
            f"description claims {stated.group(1)} tools for {mode}, "
            f"but the surface serves {served}")


def test_served_surface_is_the_set_plus_the_mode_tool(tmp_path):
    """The filtered modes serve their set and *exactly* one extra: the way out.

    This is the +1 that makes the sets read as 9/17 while hosts see 10/18. It is
    an invariant, not an accident: nothing else may slip past the mode filter.
    """
    for mode, allowed in (("lite", modes.LITE_TOOLS),
                          ("balanced", modes.BALANCED_TOOLS)):
        assert _names(mode, tmp_path) == set(allowed) | {"conscio_mode"}


def test_remember_is_present_in_every_mode(tmp_path):
    for mode in modes.MODES:
        assert "conscio_remember" in _names(mode, tmp_path), f"no memory write in {mode}"


def test_mode_tool_exists_in_every_mode(tmp_path):
    for mode in modes.MODES:
        assert "conscio_mode" in _names(mode, tmp_path), f"no way out of {mode}"


def test_mode_read_reports_current_surface(tmp_path):
    import json
    b, engine = _bindings(tmp_path, mode="balanced")
    try:
        out = json.loads(b.call_tool("conscio_mode", {})["content"][0]["text"])
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
            b.call_tool("conscio_mode", {"set": "lite"})["content"][0]["text"])
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
            b.call_tool("conscio_mode", {"set": "turbo"})
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
    `conscio_act` sem erro. Verificado no baseline antes de escrever este teste.
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
    assert "conscio_act" in names, "explicit --enable-act was silently dropped by lite"
    assert len(names) == 10 + len(schemas.ACT_TOOL_DEFS)
    act = next(d for d in defs if d["name"] == "conscio_act")
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


def test_mode_rejects_guessed_argument_name(tmp_path):
    """Uma chave errada levanta; não passa por leitura silenciosa.

    Achado no smoke real da v4.0: `{"mode": "lite"}` devolvia o modo ATUAL, e a
    resposta é indistinguível de uma troca bem-sucedida — o modelo "trocou" pra
    lite e seguiu com 35 tools. Mesma armadilha do `--since` do GitSensor: um
    argumento recusado tem que doer, não parecer sucesso.
    """
    from conscio.mcp import jsonrpc

    b, engine = _bindings(tmp_path, mode="ultra")
    try:
        with pytest.raises(jsonrpc.InvalidParams) as exc:
            b.call_tool("conscio_mode", {"mode": "lite"})
        assert "unknown argument" in str(exc.value)
        assert b.mode == "ultra"                 # não moveu a superfície
        assert b.drain_notifications() == []     # nem avisou o host
        assert b._mode_toggler({})["mode"] == "ultra"   # leitura ainda funciona
    finally:
        engine.close()
    assert modes.read_mode(tmp_path) is None     # nem tocou no disco


def test_mode_schema_is_closed():
    """O host também precisa conseguir recusar antes da chamada."""
    schema = schemas.MODE_TOOL_DEF["inputSchema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["set"]["enum"] == list(modes.MODES)
