# tests/test_liaison_relay_net.py
"""Tests for conscio.liaison.relay_net — cross-machine relay transport.

The relay works over a shared liaison.db today (same filesystem). For
agents on DIFFERENT machines (local network / tailscale) this module
provides an HTTP bridge: a small server that receives relay messages POSTed
by peer machines and writes them into the local liaison.db, plus a client
that POSTs a message to a peer's endpoint. Tailscale just makes the peer
reachable at a MagicDNS/100.x address — the transport is plain HTTP.

Auth: a shared token (CONSCIO_RELAY_TOKEN) required on POST. The tailnet is
already a private network, but the token keeps /api-layer safety minimal.
"""
import json
import socket
import threading

import pytest

from conscio.liaison import mailbox, relay, relay_net


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def db(tmp_path):
    return tmp_path / "liaison.db"


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
    def test_writes_to_local_mailbox(self, db):
        self_id = "local"
        peer = "remote-machine"
        relay_net.handle_inbound(db, self_id, {
            "from": peer, "to": self_id, "type": "chat",
            "payload": {"text": "oi da rede"}})
        inbox = mailbox.inbox(db, self_id, unread_only=False)
        assert inbox and inbox[0]["payload"]["text"] == "oi da rede"
        assert inbox[0]["from_instance"] == peer

    def test_to_other_ignored_unless_self_default(self, db):
        # to não local: cai para self_id (o receptor local é o dono do nó)
        self_id = "local"
        relay_net.handle_inbound(db, self_id, {
            "from": "remote", "to": "somewhere-else", "type": "chat",
            "payload": {"text": "oi"}})
        inbox = mailbox.inbox(db, self_id, unread_only=False)
        assert inbox and inbox[0]["to_instance"] == self_id

    def test_graceful_on_bad_payload(self, db):
        self_id = "s"
        # sem erro / sem travar
        relay_net.handle_inbound(db, self_id, {
            "from": "r", "to": "s", "type": "chat",
            "payload": {"s": "y" * (relay.MAX_PAYLOAD_BYTES + 10)}})
        assert mailbox.inbox(db, self_id, unread_only=False) == []


class TestServerClient:
    def test_roundtrip_over_http(self, db):
        self_id = "local-node"
        srv = relay_net.make_server("127.0.0.1", _port(), "sekret",
                                    db, self_id)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = srv.server_address[:2]
            url = f"http://{host}:{port}"
            ok = relay_net.transport_send(url,
                                          {"from": "peer-x",
                                           "to": self_id,
                                           "type": "chat",
                                           "payload": {"text": "via rede"}},
                                          token="sekret")
            assert ok is True
            inbox = mailbox.inbox(db, self_id, unread_only=False)
            assert inbox and inbox[0]["payload"]["text"] == "via rede"
        finally:
            srv.shutdown(); srv.server_close()

    def test_transport_rejects_wrong_token(self, db):
        self_id = "local-node"
        srv = relay_net.make_server("127.0.0.1", _port(), "sekret", db, self_id)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = srv.server_address[:2]
            ok = relay_net.transport_send(
                f"http://{host}:{port}",
                {"from": "peer-x", "to": self_id, "type": "chat",
                 "payload": {"text": "oi"}},
                token="errado")
            assert ok is False
            assert mailbox.inbox(db, self_id, unread_only=False) == []
        finally:
            srv.shutdown(); srv.server_close()

    def test_transport_unreachable_returns_false(self, db):
        ok = relay_net.transport_send(
            "http://127.0.0.1:1/none",
            {"from": "a", "to": "b", "type": "chat", "payload": {}},
            token="x")
        assert ok is False


class TestHealthEndpoint:
    def _server(self, db, token="sekret"):
        self_id = "local-node"
        srv = relay_net.make_server("127.0.0.1", _port(), token, db, self_id)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        return srv, self_id

    def _get(self, srv, path: str = "/relay/health",
             token: str | None = "sekret"):
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

    def test_health_ok_with_token(self, db):
        srv, self_id = self._server(db)
        try:
            status, body = self._get(srv)
            assert status == 200
            data = json.loads(body)
            assert data["ok"] is True
            assert data["self_id"] == self_id
            assert isinstance(data["agents_alive"], list)
        finally:
            srv.shutdown(); srv.server_close()

    def test_health_requires_token(self, db):
        srv, _ = self._server(db)
        try:
            status, _ = self._get(srv, token=None)
            assert status == 401
        finally:
            srv.shutdown(); srv.server_close()

    def test_health_wrong_token(self, db):
        srv, _ = self._server(db)
        try:
            status, _ = self._get(srv, token="errado")
            assert status == 401
        finally:
            srv.shutdown(); srv.server_close()

    def test_health_unknown_path_404(self, db):
        srv, _ = self._server(db)
        try:
            status, _ = self._get(srv, path="/relay/outro")
            assert status == 404
        finally:
            srv.shutdown(); srv.server_close()