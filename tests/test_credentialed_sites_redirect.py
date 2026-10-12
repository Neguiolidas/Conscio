"""Tests verifying that credentialed HTTP calls refuse redirects and protect secrets (Slice 3)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from conscio.decision_adapter import DecisionAdapter, DecisionError
from conscio.hub.providers import probe_models
from conscio.liaison.relay_net import ACCEPTED, REJECTED, transport_post
from conscio.session_rag import OpenAICompatibleEmbedder


def _start_dual_servers(
    redirect_status: int = 302,
    response_body_a: bytes = b"{}",
    response_body_b: bytes = b"{}",
) -> tuple[int, int, list[dict[str, str]], tuple[HTTPServer, HTTPServer]]:
    received_requests_b: list[dict[str, str]] = []

    class _HandlerB(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received_requests_b.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(response_body_b)

        def do_POST(self) -> None:
            received_requests_b.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(response_body_b)

        def log_message(self, *args: Any) -> None:
            pass

    server_b = HTTPServer(("127.0.0.1", 0), _HandlerB)
    thread_b = threading.Thread(target=server_b.serve_forever, daemon=True)
    thread_b.start()
    port_b = server_b.server_port

    class _HandlerA(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if redirect_status:
                self.send_response(redirect_status)
                self.send_header("Location", f"http://127.0.0.1:{port_b}/target")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(response_body_a)

        def do_POST(self) -> None:
            if redirect_status:
                self.send_response(redirect_status)
                self.send_header("Location", f"http://127.0.0.1:{port_b}/target")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(response_body_a)

        def log_message(self, *args: Any) -> None:
            pass

    server_a = HTTPServer(("127.0.0.1", 0), _HandlerA)
    thread_a = threading.Thread(target=server_a.serve_forever, daemon=True)
    thread_a.start()
    port_a = server_a.server_port

    return port_a, port_b, received_requests_b, (server_a, server_b)


def _stop_servers(servers: tuple[HTTPServer, HTTPServer]) -> None:
    for s in servers:
        try:
            s.shutdown()
            s.server_close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Site 1: DecisionAdapter (POST with Authorization: Bearer api_key)
# ---------------------------------------------------------------------------
def test_decision_adapter_redirect_refused_b_receives_zero_requests() -> None:
    port_a, _port_b, requests_b, servers = _start_dual_servers(redirect_status=302)
    try:
        adapter = DecisionAdapter(
            url=f"http://127.0.0.1:{port_a}/decide",
            model="decision-model",
            api_key_env="TEST_DECISION_KEY",
            api_key_file=None,
            timeout_s=5.0,
            api_key="secret-decision-key-123",
        )
        questions = {
            "q_choice": {
                "type": "choice",
                "question": "proceed or hold?",
                "criteria": ["proceed", "hold"],
            }
        }
        with pytest.raises(DecisionError) as exc_info:
            adapter.decide(state={"context": "test"}, questions=questions)

        assert len(requests_b) == 0, f"Server B received redirected requests: {requests_b}"
        assert str(exc_info.value) == "http_302"
    finally:
        _stop_servers(servers)


def test_decision_adapter_legitimate_200() -> None:
    canonical_body = json.dumps({
        "model": "decision-model",
        "answers": {
            "q_choice": {
                "type": "choice",
                "choice": "proceed",
                "probabilities": {"proceed": 0.95, "hold": 0.05},
                "confidence": 0.95,
            }
        },
    }).encode("utf-8")
    port_a, _port_b, requests_b, servers = _start_dual_servers(
        redirect_status=0, response_body_a=canonical_body
    )
    try:
        adapter = DecisionAdapter(
            url=f"http://127.0.0.1:{port_a}/decide",
            model="decision-model",
            api_key_env="TEST_DECISION_KEY",
            api_key_file=None,
            timeout_s=5.0,
            api_key="secret-decision-key-123",
        )
        questions = {
            "q_choice": {
                "type": "choice",
                "question": "proceed or hold?",
                "criteria": ["proceed", "hold"],
            }
        }
        res = adapter.decide(state={"context": "test"}, questions=questions)
        assert res.model == "decision-model"
        assert res.answers["q_choice"].value == "proceed"
        assert len(requests_b) == 0
    finally:
        _stop_servers(servers)


# ---------------------------------------------------------------------------
# Site 2: hub/providers.probe_models (GET with Authorization: Bearer)
# ---------------------------------------------------------------------------
def test_hub_providers_redirect_refused_b_receives_zero_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port_a, _port_b, requests_b, servers = _start_dual_servers(redirect_status=302)
    monkeypatch.setenv("TEST_OPENAI_KEY", "secret-provider-key-456")
    try:
        cfg = {
            "type": "openai",
            "base_url": f"http://127.0.0.1:{port_a}",
            "api_key_env": "TEST_OPENAI_KEY",
        }
        res = probe_models(cfg, refresh=True)
        assert len(requests_b) == 0, f"Server B received redirected requests: {requests_b}"
        assert res["source"] == "fallback"
    finally:
        _stop_servers(servers)


def test_hub_providers_legitimate_200(monkeypatch: pytest.MonkeyPatch) -> None:
    models_body = json.dumps({"data": [{"id": "gpt-4o"}]}).encode("utf-8")
    port_a, _port_b, requests_b, servers = _start_dual_servers(
        redirect_status=0, response_body_a=models_body
    )
    monkeypatch.setenv("TEST_OPENAI_KEY", "secret-provider-key-456")
    try:
        cfg = {
            "type": "openai",
            "base_url": f"http://127.0.0.1:{port_a}",
            "api_key_env": "TEST_OPENAI_KEY",
        }
        res = probe_models(cfg, refresh=True)
        assert res["source"] == "api"
        assert "gpt-4o" in res["models"]
        assert len(requests_b) == 0
    finally:
        _stop_servers(servers)


# ---------------------------------------------------------------------------
# Site 3: liaison/relay_net.transport_post (POST with Authorization: Bearer)
# ---------------------------------------------------------------------------
def test_relay_net_redirect_refused_b_receives_zero_requests() -> None:
    port_a, _port_b, requests_b, servers = _start_dual_servers(redirect_status=302)
    try:
        res = transport_post(
            base_url=f"http://127.0.0.1:{port_a}",
            msg={"type": "ping", "data": "hello"},
            token="secret-relay-token-789",
            timeout=5.0,
        )
        assert len(requests_b) == 0, f"Server B received redirected requests: {requests_b}"
        assert res == REJECTED
    finally:
        _stop_servers(servers)


def test_relay_net_legitimate_200() -> None:
    port_a, _port_b, requests_b, servers = _start_dual_servers(
        redirect_status=0, response_body_a=b'{"status": "ok"}'
    )
    try:
        res = transport_post(
            base_url=f"http://127.0.0.1:{port_a}",
            msg={"type": "ping", "data": "hello"},
            token="secret-relay-token-789",
            timeout=5.0,
        )
        assert res == ACCEPTED
        assert len(requests_b) == 0
    finally:
        _stop_servers(servers)


# ---------------------------------------------------------------------------
# Site 4: session_rag.OpenAICompatibleEmbedder (POST with Authorization: Bearer)
# ---------------------------------------------------------------------------
def test_session_rag_redirect_refused_b_receives_zero_requests() -> None:
    port_a, _port_b, requests_b, servers = _start_dual_servers(redirect_status=302)
    try:
        embedder = OpenAICompatibleEmbedder(
            model="text-embedding-3-small",
            url=f"http://127.0.0.1:{port_a}/v1/embeddings",
            api_key="secret-rag-key-012",
        )
        vec = embedder.embed("test text to embed")
        assert vec == []
        assert len(requests_b) == 0, f"Server B received redirected requests: {requests_b}"
    finally:
        _stop_servers(servers)


def test_session_rag_legitimate_200() -> None:
    embed_body = json.dumps({"data": [{"embedding": [0.1, 0.2, 0.3]}]}).encode("utf-8")
    port_a, _port_b, requests_b, servers = _start_dual_servers(
        redirect_status=0, response_body_a=embed_body
    )
    try:
        embedder = OpenAICompatibleEmbedder(
            model="text-embedding-3-small",
            url=f"http://127.0.0.1:{port_a}/v1/embeddings",
            api_key="secret-rag-key-012",
        )
        vec = embedder.embed("test text to embed")
        assert vec == [0.1, 0.2, 0.3]
        assert len(requests_b) == 0
    finally:
        _stop_servers(servers)
