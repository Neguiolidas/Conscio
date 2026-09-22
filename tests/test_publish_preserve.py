"""v4.6.8: publish_self — o cartão do agente sobrevive ao escritor que não sabe.

Três classes de apagamento medidas no artefato 4.6.7 (memória id 45):
1. publish_self(modelo='', ...) sobre cartão rico APAGA modelo/familia/runtime/papel
2. capabilities regride ao default ('relay',) quando o caller não passa nada
3. chave desconhecida (futura) some porque o card é reconstruído do zero

O consenso do brainstorm (Hermet/Claude/Gemini, dono ratificou): read-modify-write
com sentinela. None = "não sei, preserva" / valor = "escreve". Subsume o bloco
`herdado` do space e o laço de halls — duas regras ad-hoc viram uma.
"""
import json

import pytest

from conscio.liaison import directory


@pytest.fixture
def card_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(directory, "peers_dir", lambda: tmp_path)
    return tmp_path


def _publish(card_dir, iid, **kw):
    return directory.publish_self(iid, **kw)


def _must_get(iid):
    card = directory.get(iid)
    assert card is not None, f"card for {iid} should exist"
    return card


class TestPublishPreserves:
    def test_rich_card_survives_blind_republish(self, card_dir):
        """Um publish rico seguido de um publish cego (reactor) não pode apagar."""
        _publish(card_dir, "agent-a", modelo="glm-5.3", runtime="hermes",
                 familia="test", papel="executor",
                 capabilities=("relay", "halls"))
        # o reactor republica sem saber nada — tudo default/None
        _publish(card_dir, "agent-a")
        card = _must_get("agent-a")
        assert card["modelo"] == "glm-5.3"
        assert card["runtime"] == "hermes"
        assert card["familia"] == "test"
        assert card["papel"] == "executor"
        assert card["capabilities"] == ["relay", "halls"]

    def test_explicit_value_overwrites(self, card_dir):
        """Valor explícito (não-None) vence — o servidor que sabe, escreve."""
        _publish(card_dir, "agent-a", modelo="glm-5.3")
        _publish(card_dir, "agent-a", modelo="glm-5.4")
        card = _must_get("agent-a")
        assert card["modelo"] == "glm-5.4"

    def test_unknown_key_survives(self, card_dir):
        """Chave que esta versão não conhece não pode sumir no refresh."""
        _publish(card_dir, "agent-a", modelo="x")
        # simula um campo futuro escrito por outro caminho
        card_path = card_dir / "agent-a.json"
        card = json.loads(card_path.read_text(encoding="utf-8"))
        card["future_field"] = "precious"
        card_path.write_text(json.dumps(card), encoding="utf-8")
        _publish(card_dir, "agent-a")
        card = _must_get("agent-a")
        assert card["future_field"] == "precious"

    def test_space_still_wins_when_explicit(self, card_dir):
        """A regra do space (valor novo vence quando presente) continua válida."""
        _publish(card_dir, "agent-a", space="/old/space")
        _publish(card_dir, "agent-a", space="/new/space")
        card = _must_get("agent-a")
        assert card["space"] == "/new/space"

    def test_space_inherited_when_omitted(self, card_dir):
        """Republish cego herda o space — comportamento 4.6.7 intacto."""
        _publish(card_dir, "agent-a", space="/my/space")
        _publish(card_dir, "agent-a")
        card = _must_get("agent-a")
        assert card["space"] == "/my/space"

    def test_halls_still_survive(self, card_dir):
        """O laço de halls (chaves do agente) continua preservado."""
        _publish(card_dir, "agent-a", modelo="x")
        card_path = card_dir / "agent-a.json"
        card = json.loads(card_path.read_text(encoding="utf-8"))
        card["halls"] = ["hall-1"]
        card_path.write_text(json.dumps(card), encoding="utf-8")
        _publish(card_dir, "agent-a")
        card = _must_get("agent-a")
        assert card["halls"] == ["hall-1"]

    def test_capabilities_default_not_reverting(self, card_dir):
        """Republish cego não regride capabilities ao default do parâmetro."""
        _publish(card_dir, "agent-a", capabilities=("relay", "halls"))
        _publish(card_dir, "agent-a")
        card = _must_get("agent-a")
        assert card["capabilities"] == ["relay", "halls"]
