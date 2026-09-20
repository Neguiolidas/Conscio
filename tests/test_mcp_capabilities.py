import pytest

from conscio.mcp import capabilities as caps


def test_the_space_supplies_the_capability_without_a_flag(tmp_path):
    caps.write_capabilities(tmp_path, ["relay"])
    assert caps.resolve_capability(tmp_path, "relay", False) is True


def test_absent_marker_and_absent_flag_means_off(tmp_path):
    assert caps.resolve_capability(tmp_path, "relay", False) is False


def test_the_flag_turns_it_on_even_without_a_marker(tmp_path):
    """--enable-relay e store_true: ausencia significa 'nao especificado',
    nunca 'desligado'. Por isso a flag LIGA e nunca desliga -- precedencia
    diferente, de proposito, da de resolve_mode."""
    assert caps.resolve_capability(tmp_path, "relay", True) is True


def test_an_unknown_capability_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        caps.write_capabilities(tmp_path, ["telepatia"])


def _server_with(tmp_path, relay):
    """Servidor de verdade (passa pelo __init__), formato de _real_server
    em tests/test_relay_plug_and_play.py:222 -- monta Bindings com relay=."""
    from conscio.engine import ConsciousnessEngine
    from conscio.mcp.seen import SeenStore
    from conscio.mcp.server import Bindings
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    seen = SeenStore(tmp_path / "mcp_seen.db")
    b = Bindings(eng, seen, adapter_name=None, workspace_id="ws",
                 self_instance_id="live-a",
                 liaison_db=tmp_path / "live-a.db", relay=relay)
    return b


def test_a_server_without_the_flag_announces_relay_from_the_space(tmp_path):
    """O caminho do plugin: o .mcp.json do asset NUNCA carrega a flag."""
    caps.write_capabilities(tmp_path, ["relay"])
    ligado = caps.resolve_capability(tmp_path, "relay", False)
    srv = _server_with(tmp_path, relay=ligado)
    nomes = {d["name"] for d in srv.tool_defs()}
    assert "conscio_relay" in nomes


def test_without_marker_and_without_flag_relay_is_not_announced(tmp_path):
    from conscio.mcp import capabilities as caps
    srv = _server_with(tmp_path, relay=caps.resolve_capability(
        tmp_path, "relay", False))
    assert "conscio_relay" not in {d["name"] for d in srv.tool_defs()}


def test_legacy_space_with_liaison_db_auto_migrates(tmp_path):
    (tmp_path / "liaison.db").touch()
    assert caps.resolve_capability(tmp_path, "relay", False) is True
    assert "relay" in caps.read_capabilities(tmp_path)


def test_cli_flag_persists_capability_to_space(tmp_path):
    assert caps.resolve_capability(tmp_path, "relay", True) is True
    assert "relay" in caps.read_capabilities(tmp_path)

