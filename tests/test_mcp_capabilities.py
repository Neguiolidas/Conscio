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



# ── v4.6.5: write-through — a flag vista uma vez vira consentimento durável ──

def test_a_cli_flag_is_persisted_on_first_sight(tmp_path):
    """Host configurado a mao se cura sozinho: depois da primeira subida a
    capacidade deixa de depender do arg que o proximo update apaga."""
    assert caps.resolve_capability(tmp_path, "relay", True) is True
    assert caps.read_capabilities(tmp_path) == {"relay"}


def test_persisting_does_not_drop_what_was_already_granted(tmp_path):
    caps.write_capabilities(tmp_path, ["halls"])
    caps.resolve_capability(tmp_path, "relay", True)
    assert caps.read_capabilities(tmp_path) == {"halls", "relay"}


def test_resolving_without_a_flag_writes_nothing(tmp_path):
    """Leitura nao pode criar arquivo. Um espaco sem consentimento continua
    sem consentimento -- senao o proprio ato de perguntar concederia."""
    assert caps.resolve_capability(tmp_path, "relay", False) is False
    assert not caps.capabilities_path(tmp_path).exists()


def test_an_existing_liaison_db_does_not_grant_consent(tmp_path):
    """Ruling da v4.6.5: consentimento nasce de ACAO PRESENTE do usuario,
    nunca de artefato retroativo.

    `liaison.db` existe porque o agente RECEBEU MENSAGEM, nao porque alguem
    consentiu. Conceder por causa dele ressuscita o consentimento de quem
    tirou --enable-relay do .mcp.json a mao -- que era a UNICA forma de
    revogar antes desta versao, e e exatamente a populacao atingida pelo A3.
    """
    (tmp_path / "liaison.db").touch()
    assert caps.resolve_capability(tmp_path, "relay", False) is False
    assert not caps.capabilities_path(tmp_path).exists()
