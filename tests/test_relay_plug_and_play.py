"""v4.5.4 — o servidor publica e descobre pelo diretório (A1, A2, C2)."""
from __future__ import annotations

import time

import pytest

from conscio.liaison import directory


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))


def _server(tmp_path, self_id="agent-a", relay_peers=()):
    from conscio.mcp.server import Bindings
    srv = Bindings.__new__(Bindings)      # sem engine
    srv.self_instance_id = self_id
    srv.liaison_db = tmp_path / f"{self_id}.db"
    srv.relay_peers = tuple(relay_peers)
    srv.identity_model = "m"
    srv.identity_familia = "f"
    srv.identity_runtime = "r"
    srv.identity_papel = "executor"
    srv.card_error = ""
    srv._card_ts = 0.0
    return srv


def test_ensure_registered_publishes_card(tmp_path):
    srv = _server(tmp_path)
    srv._ensure_registered()
    assert directory.get("agent-a")["instance_id"] == "agent-a"
    assert srv.card_error == ""


def test_resolve_peers_reads_directory_not_own_db(tmp_path):
    """Com db por agente, o registro local só conhece a si mesmo (A1/A2)."""
    directory.publish({"instance_id": "agent-b", "spool": "x", "url": "",
                       "updated_at": time.time()})
    srv = _server(tmp_path)
    assert srv._resolve_peers() == {"agent-b"}


def test_stale_peer_still_resolvable(tmp_path):
    directory.publish({"instance_id": "agent-b", "spool": "x", "url": "",
                       "updated_at": time.time() - 10 * 3600})
    assert _server(tmp_path)._resolve_peers() == {"agent-b"}


def test_relay_peer_flag_restricts_instead_of_requiring(tmp_path):
    for cid in ("agent-b", "agent-c"):
        directory.publish({"instance_id": cid, "spool": "x", "url": "",
                           "updated_at": time.time()})
    srv = _server(tmp_path, relay_peers=("agent-c",))
    assert srv._resolve_peers() == {"agent-c"}


def test_configured_peer_survives_an_empty_directory(tmp_path):
    """Peer de outra máquina (remotes.json/Tailscale) não tem cartão aqui.
    Interseção com o diretório o apagaria em silêncio."""
    srv = _server(tmp_path, relay_peers=("hae-remote",))
    assert srv._resolve_peers() == {"hae-remote"}


def test_card_publish_failure_is_visible_not_swallowed(tmp_path, monkeypatch):
    def boom(card):
        raise OSError("read-only fs")

    monkeypatch.setattr(directory, "publish", boom)
    srv = _server(tmp_path)
    srv._ensure_registered()                    # não pode levantar
    assert "read-only fs" in srv.card_error


def test_directory_is_projected_into_local_registry(tmp_path):
    """Com db por agente, ninguém mais escreve na minha tabela `agents`.
    O observatory e `conscio_agents` leem dela — sem projeção, a sociedade
    inteira some da visão local. (Halls não: depois da Task 3b eles leem o
    diretório direto.)"""
    from conscio.liaison import agents
    directory.publish({"instance_id": "agent-b", "spool": "s", "url": "",
                       "modelo": "opus", "familia": "claude", "runtime": "cc",
                       "papel": "executor", "updated_at": time.time()})
    srv = _server(tmp_path)
    srv._ensure_registered()
    rows = {a["instance_id"]: a for a in agents.list_agents(srv.liaison_db,
                                                            include_stale=True)}
    assert set(rows) == {"agent-a", "agent-b"}
    assert rows["agent-b"]["familia"] == "claude"     # identidade veio no cartão


def test_projection_does_not_resurrect_a_dead_peer(tmp_path):
    """O heartbeat projetado é o do cartão. Com o relógio local, um agente
    extinto ficaria 'vivo' para sempre a cada tick do vizinho."""
    from conscio.liaison import agents
    directory.publish({"instance_id": "agent-old", "spool": "s", "url": "",
                       "updated_at": time.time() - 10 * 3600})
    srv = _server(tmp_path)
    srv._ensure_registered()
    live = {a["instance_id"] for a in agents.list_agents(srv.liaison_db,
                                                         include_stale=False)}
    assert "agent-old" not in live


def test_card_republish_is_throttled(tmp_path, monkeypatch):
    calls = []
    real = directory.publish
    monkeypatch.setattr(directory, "publish",
                        lambda c: (calls.append(1), real(c))[1])
    srv = _server(tmp_path)
    srv._ensure_registered()
    srv._ensure_registered()
    assert len(calls) == 1


def test_projection_survives_a_broken_card(tmp_path):
    """Um cartão sem instance_id não pode derrubar a projeção inteira."""
    from conscio.liaison import agents
    directory.publish({"instance_id": "agent-b", "spool": "s", "url": "",
                       "updated_at": time.time()})
    (directory.peers_dir() / "junk.json").write_text("{not json",
                                                     encoding="utf-8")
    srv = _server(tmp_path)
    srv._ensure_registered()
    ids = {a["instance_id"] for a in agents.list_agents(srv.liaison_db,
                                                        include_stale=True)}
    assert ids == {"agent-a", "agent-b"}


def test_relay_peers_tool_lists_directory(tmp_path):
    directory.publish({"instance_id": "agent-b", "spool": "s", "url": "",
                       "modelo": "opus", "papel": "executor",
                       "updated_at": time.time()})
    directory.publish({"instance_id": "agent-r", "spool": "", "url": "http://h:1",
                       "updated_at": time.time() - 10 * 3600})
    out = _server(tmp_path)._relay_peers_tool({})
    by_id = {p["instance_id"]: p for p in out["peers"]}
    assert by_id["agent-b"]["reachability"] == "local"
    assert by_id["agent-b"]["alive"] is True
    assert by_id["agent-b"]["model"] == "opus"      # v4.5.4: superfície em inglês
    assert by_id["agent-b"]["role"] == "executor"
    assert by_id["agent-r"]["reachability"] == "remote"
    assert by_id["agent-r"]["alive"] is False
    assert out["self"] == "agent-a"


def test_relay_peers_tool_flags_a_peer_it_knows_nothing_about(tmp_path):
    """Peer nomeado à mão que nunca publicou cartão: continua endereçável, mas
    dizer `local` mandaria o humano procurar um spool que não existe."""
    out = _server(tmp_path, relay_peers=("ghost",))._relay_peers_tool({})
    ghost = next(p for p in out["peers"] if p["instance_id"] == "ghost")
    assert ghost["known"] is False
    assert ghost["reachability"] == "unknown"
    assert ghost["alive"] is False
    assert ghost["model"] == ""


def test_relay_peers_tool_surfaces_card_error(tmp_path):
    srv = _server(tmp_path)
    srv.card_error = "cartão não publicado: read-only fs"
    assert "read-only" in srv._relay_peers_tool({})["card_error"]


# ── Task 7: recepção simétrica ────────────────────────────────────────────

def test_inbox_ingests_spool_first(tmp_path):
    """Ninguém escreve no meu banco: o que chega, chega pelo meu spool."""
    from conscio.liaison import spool
    directory.publish({"instance_id": "agent-b", "spool": "s", "url": "",
                       "updated_at": time.time()})
    srv = _server(tmp_path)
    spool.deposit("agent-a", {"from": "agent-b", "to": "agent-a",
                              "type": "relay", "payload": {"oi": 1}})
    out = srv._relay_inbox({})
    assert [m["payload"] for m in out["messages"]] == [{"oi": 1}]


def test_empty_allowlist_no_longer_eats_messages(tmp_path):
    """A1: allowlist vazia era nega-tudo e a mensagem sumia marcada como lida."""
    from conscio.liaison import spool
    directory.publish({"instance_id": "agent-b", "spool": "s", "url": "",
                       "updated_at": time.time()})
    srv = _server(tmp_path, relay_peers=())
    spool.deposit("agent-a", {"from": "agent-b", "to": "agent-a",
                              "type": "relay", "payload": {"x": 1}})
    assert len(srv._relay_inbox({})["messages"]) == 1


def test_named_peers_still_restrict_who_is_surfaced(tmp_path):
    """Vazio = sem restrição, mas quem nomeia peers continua restringindo."""
    from conscio.liaison import spool
    srv = _server(tmp_path, relay_peers=("agent-b",))
    spool.deposit("agent-a", {"from": "stranger", "to": "agent-a",
                              "type": "relay", "payload": {"x": 1}})
    assert srv._relay_inbox({})["messages"] == []


def test_inbox_and_send_share_one_peer_source(tmp_path):
    """A2: mesma função dos dois lados — não dá para mandar e não receber."""
    import inspect

    from conscio.mcp.server import Bindings
    src = inspect.getsource(Bindings._relay_inbox)
    assert "self.relay_peers" not in src
    assert "_resolve_peers" in src


def test_own_outbox_copy_never_shows_in_inbox(tmp_path):
    """I5: a cópia de outbox (to_instance=peer) mora no MESMO db agora."""
    from conscio.liaison import mailbox
    srv = _server(tmp_path)
    mailbox.send(srv.liaison_db, from_instance="agent-a", to_instance="agent-b",
                 type="relay", payload={"mine": 1})
    assert srv._relay_inbox({})["messages"] == []


# ── v4.5.4 C5: reatividade nasce com a sessão MCP, sem systemd ────────────

def _real_server(tmp_path, instance_id="live-a", relay=True):
    """Servidor de verdade (passa pelo __init__), que é onde a thread nasce."""
    from conscio.engine import ConsciousnessEngine
    from conscio.mcp.seen import SeenStore
    from conscio.mcp.server import Bindings
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    seen = SeenStore(tmp_path / "mcp_seen.db")
    b = Bindings(eng, seen, adapter_name=None, workspace_id="ws",
                 self_instance_id=instance_id,
                 liaison_db=tmp_path / f"{instance_id}.db", relay=relay)
    return b, eng, seen


def test_mcp_server_starts_reactor_when_hook_is_set(tmp_path, monkeypatch):
    """Ninguém arma nada: existe hook + relay ligado, existe reatividade."""
    import time

    from conscio.liaison import spool
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))
    got = tmp_path / "woken.json"
    monkeypatch.setenv("CONSCIO_NOTIFY_CMD", f"cat > {got}")
    srv, eng, seen = _real_server(tmp_path)
    try:
        assert srv.reactor_thread is not None
        spool.deposit(srv.self_instance_id,
                      {"from": "b", "to": srv.self_instance_id,
                       "type": "relay", "payload": {"text": "acorda"}})
        for _ in range(100):                       # <= 5s
            if got.exists():
                break
            time.sleep(0.05)
        assert got.exists(), "a sessão não reagiu à mensagem depositada"
        assert "acorda" in got.read_text()
    finally:
        srv.reactor_thread.stop()
        seen.close()
        eng.close()


def test_no_hook_no_thread(tmp_path, monkeypatch):
    monkeypatch.delenv("CONSCIO_NOTIFY_CMD", raising=False)
    srv, eng, seen = _real_server(tmp_path, instance_id="live-b")
    try:
        assert srv.reactor_thread is None
    finally:
        seen.close()
        eng.close()
