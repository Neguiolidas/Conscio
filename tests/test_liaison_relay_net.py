# tests/test_liaison_relay_net.py
"""Tests for conscio.liaison.relay_net — the cross-machine bridge.

The bridge is a postman, not a mailbox (v4.5.4): it owns no database, and an
inbound message is deposited in the RECIPIENT's spool. One bridge per machine
therefore serves every agent on that machine, and the agent that started it
gets no special treatment.

Auth: a shared token on every request. Tailscale only makes the peer
reachable at a MagicDNS/100.x address — the transport is plain HTTP.
"""
import json
import socket
import threading

import pytest

from conscio.liaison import directory, relay, relay_net, spool


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(autouse=True)
def _relay_root(tmp_path, monkeypatch):
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))


def _known(*ids):
    for cid in ids:
        directory.publish({"instance_id": cid,
                           "spool": str(directory.spool_dir(cid)), "url": ""})


class TestValidateMsg:
    def test_valid_msg_passes(self):
        relay_net.validate_msg({
            "from": "a", "to": "b", "type": "chat", "payload": {"t": "oi"}})

    def test_missing_fields_rejected(self):
        for bad in ({}, {"from": "a"}, {"from": "a", "to": "b"},
                    {"from": "a", "to": "b", "type": "chat"}):
            with pytest.raises(ValueError):
                relay_net.validate_msg(bad)

    def test_reserved_type_rejected(self):
        with pytest.raises(ValueError):
            relay_net.validate_msg({
                "from": "a", "to": "b", "type": "review_request",
                "payload": {}})

    def test_oversize_payload_rejected(self):
        with pytest.raises(ValueError):
            relay_net.validate_msg({
                "from": "a", "to": "b", "type": "chat",
                "payload": {"x": "y" * (relay.MAX_PAYLOAD_BYTES + 10)}})


class TestHandleInbound:
    def test_lands_in_recipient_spool_not_bridge_owner(self):
        """A11: um bridge por máquina serve todos os agentes locais."""
        _known("agent-b")
        status, _ = relay_net.handle_inbound({
            "from": "a", "to": "agent-b", "type": "relay",
            "payload": {"x": 1}})
        assert status == 200
        assert len(list(directory.spool_dir("agent-b").glob("*.json"))) == 1

    def test_recipient_ingests_what_the_bridge_left(self, tmp_path):
        """A entrega remota termina no mesmo lugar que a local: o db do dono."""
        from conscio.liaison import mailbox
        _known("agent-b")
        relay_net.handle_inbound({"from": "a", "to": "agent-b",
                                  "type": "relay",
                                  "payload": {"text": "de outra máquina"}})
        db = tmp_path / "b.db"
        assert spool.ingest(db, "agent-b") == 1
        got = mailbox.inbox(db, "agent-b", unread_only=True)
        assert got[0]["payload"]["text"] == "de outra máquina"

    def test_unknown_recipient_is_404(self):
        status, _ = relay_net.handle_inbound({"from": "a", "to": "nobody",
                                              "type": "relay", "payload": {}})
        assert status == 404

    def test_traversal_in_to_is_rejected(self, tmp_path):
        """I1: `to` vem da REDE — sem validação isso escreve fora do root."""
        status, _ = relay_net.handle_inbound({"from": "a",
                                              "to": "../../../evil",
                                              "type": "relay", "payload": {}})
        assert status == 400
        assert not (tmp_path / "evil").exists()
        assert not (directory.relay_root().parent / "evil").exists()

    def test_malformed_is_400_not_a_crash(self):
        status, reason = relay_net.handle_inbound({"from": "a"})
        assert status == 400 and reason


class TestServerClient:
    def _serve(self, token="sekret"):
        srv = relay_net.make_server("127.0.0.1", _port(), token)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://{srv.server_address[0]}:{srv.server_address[1]}"

    def test_roundtrip_over_http(self):
        _known("agent-b")
        srv, url = self._serve()
        try:
            ok = relay_net.transport_send(
                url, {"from": "peer-x", "to": "agent-b", "type": "chat",
                      "payload": {"text": "via rede"}}, token="sekret")
            assert ok is True
            assert len(list(directory.spool_dir("agent-b").glob("*.json"))) == 1
        finally:
            srv.shutdown(); srv.server_close()

    def test_wrong_token_delivers_nothing(self):
        _known("agent-b")
        srv, url = self._serve()
        try:
            ok = relay_net.transport_send(
                url, {"from": "peer-x", "to": "agent-b", "type": "chat",
                      "payload": {"text": "oi"}}, token="errado")
            assert ok is False
            assert list(directory.spool_dir("agent-b").glob("*.json")) == []
        finally:
            srv.shutdown(); srv.server_close()

    def test_transport_unreachable_returns_false(self):
        ok = relay_net.transport_send(
            "http://127.0.0.1:1/none",
            {"from": "a", "to": "b", "type": "chat", "payload": {}}, token="x")
        assert ok is False

    def test_two_servers_do_not_share_class_state(self):
        """A12: estado por instância, não atributo de classe."""
        s1 = relay_net.make_server("127.0.0.1", 0, "token-1")
        s2 = relay_net.make_server("127.0.0.1", 0, "token-2")
        try:
            assert s1.relay_token == "token-1"
            assert s2.relay_token == "token-2"     # o 2º não sequestrou o 1º
        finally:
            s1.server_close(); s2.server_close()


class TestHealthEndpoint:
    def _serve(self, token="sekret"):
        srv = relay_net.make_server("127.0.0.1", _port(), token)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def _get(self, srv, path="/relay/health", token="sekret"):
        import urllib.request
        from urllib import error as urlerror
        host, port = srv.server_address[:2]
        req = urllib.request.Request(f"http://{host}:{port}{path}")
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urlerror.HTTPError as e:
            return e.code, e.read().decode()

    def test_health_lists_local_agents(self):
        _known("agent-b", "agent-c")
        srv = self._serve()
        try:
            status, body = self._get(srv)
            assert status == 200
            data = json.loads(body)
            assert data["ok"] is True and data["role"] == "bridge"
            assert set(data["agents"]) == {"agent-b", "agent-c"}
        finally:
            srv.shutdown(); srv.server_close()

    def test_health_requires_token(self):
        srv = self._serve()
        try:
            assert self._get(srv, token=None)[0] == 401
            assert self._get(srv, token="errado")[0] == 401
        finally:
            srv.shutdown(); srv.server_close()

    def test_unknown_path_404(self):
        srv = self._serve()
        try:
            assert self._get(srv, path="/relay/outro")[0] == 404
        finally:
            srv.shutdown(); srv.server_close()


class TestToken:
    def test_token_is_created_once_and_reused(self, tmp_path):
        path = tmp_path / "bridge.token"
        first = relay_net.read_or_create_token(path)
        assert first and relay_net.read_or_create_token(path) == first
        assert (path.stat().st_mode & 0o777) == 0o600
