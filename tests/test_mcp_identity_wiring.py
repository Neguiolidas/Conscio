"""v4.6.6 item 0: a identidade viaja com o consentimento.

A 4.6.5 fez a CAPACIDADE morar no espaco e deixou a IDENTIDADE presa em
`args.enable_relay`. No caminho do marketplace -- onde o .mcp.json nao carrega
a flag -- o relay era exposto e cego: self_instance_id="" fazia
mailbox.inbox(db, "") nao casar nada.
"""
import json

from conscio.liaison import mailbox
from conscio.mcp import capabilities as caps
from conscio.mcp.server import resolve_identity


def test_the_space_alone_supplies_the_identity(tmp_path):
    """O caso do marketplace: marcador no espaco, ZERO flags."""
    caps.write_capabilities(tmp_path, ["relay"])
    relay_on = caps.resolve_capability(tmp_path, "relay", False)
    self_id, db = resolve_identity(tmp_path, hermes_review=False,
                                   relay_on=relay_on)
    gravado = json.loads(
        (tmp_path / "instance.json").read_text("utf-8"))["instance_id"]
    assert self_id == gravado
    assert db is not None


def test_no_consent_means_no_identity(tmp_path):
    """Nao inventamos identidade para quem nao concedeu nada."""
    assert resolve_identity(tmp_path, hermes_review=False,
                            relay_on=False) == ("", None)


def test_the_cli_flag_still_works(tmp_path):
    """Retrocompat: host configurado na mao continua funcionando."""
    relay_on = caps.resolve_capability(tmp_path, "relay", True)
    self_id, db = resolve_identity(tmp_path, hermes_review=False,
                                   relay_on=relay_on)
    assert self_id and db is not None


def test_halls_without_relay_stays_off_and_identityless(tmp_path):
    """C9b: um hall entrega pela caixa do relay, entao halls sem relay ja e
    desligado por invariante. A correcao nao pode abrir esse contorno."""
    caps.write_capabilities(tmp_path, ["halls"])
    relay_on = caps.resolve_capability(tmp_path, "relay", False)
    halls_on = caps.resolve_capability(tmp_path, "halls", False)
    assert (relay_on, halls_on) == (False, True)
    assert resolve_identity(tmp_path, hermes_review=False,
                            relay_on=relay_on) == ("", None)


def test_relay_and_halls_together_get_an_identity(tmp_path):
    """Com relay ligado, halls volta a funcionar PELA correcao do relay:
    create_hall(owner=...) deixa de nascer com dono vazio."""
    caps.write_capabilities(tmp_path, ["relay", "halls"])
    self_id, db = resolve_identity(
        tmp_path, hermes_review=False,
        relay_on=caps.resolve_capability(tmp_path, "relay", False))
    assert self_id and db is not None


def test_the_inbox_matches_messages_addressed_to_that_identity(tmp_path):
    """O teste que teria pego o bug: com self_id vazio, inbox nao casa nada."""
    caps.write_capabilities(tmp_path, ["relay"])
    self_id, db = resolve_identity(
        tmp_path, hermes_review=False,
        relay_on=caps.resolve_capability(tmp_path, "relay", False))
    mailbox.send(db, from_instance="peer-x", to_instance=self_id,
                 type="chat", payload={"text": "oi"})
    assert len(mailbox.inbox(db, self_id, types=None, unread_only=True,
                             since_id=None, limit=50)) == 1
    assert mailbox.inbox(db, "", types=None, unread_only=True,
                         since_id=None, limit=50) == []


def test_main_wires_the_identity_from_the_space(tmp_path, monkeypatch):
    """O teste que faltava: prova a FIACAO, nao a funcao.

    Os outros chamam resolve_identity() direto, entao sabotar o main() nao
    ficava vermelho -- e o bug original morava exatamente no main().
    """
    from conscio.mcp import server as srv

    caps.write_capabilities(tmp_path, ["relay"])
    capturado = {}

    def _fake_serve(bindings, *a, **k):
        capturado["b"] = bindings

    monkeypatch.setattr(srv, "serve", _fake_serve)
    rc = srv.main(["--storage", str(tmp_path), "--model", "glm-5.1",
                   "--adapter", "mock"])

    assert rc == 0
    b = capturado["b"]
    esperado = json.loads(
        (tmp_path / "instance.json").read_text("utf-8"))["instance_id"]
    assert b.self_instance_id == esperado     # veio do espaco, sem flag
    assert b.relay is True
