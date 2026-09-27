"""Judge client tests (spec 2026-09-26, section 4).

A stub ``http.server`` on loopback port 0 stands in for the evaluate
endpoint — no real network, per the task constraints. The conftest
autouse fixture points ``adapter_config._CONFIG_PATHS`` at a
nonexistent file, so ``judge.load()`` without an explicit config dict
always sees an empty config here.
"""
from __future__ import annotations

import http.server
import json
import socket
import threading
import time
from typing import Any

import pytest
from council_bench import load_manifest

from conscio import judge

ForbiddenKeys = {"instance", "engine", "path", "agent", "relay", "identity",
                 "workspace", "model"}


def _canonical_body(
    choice: str = "veto",
    probs: tuple[float, float, float] = (0.05, 0.03, 0.92),
    confidence: float = 0.87,
    model: str = "typesafe-ai/jev",
    provider: str = "jev-canonical",
) -> dict[str, Any]:
    """A valid gateway response in the real a01 form: answers.<id> =
    {type, choice, probabilities, confidence}. The confidence is the
    model-reported value — deliberately NOT probabilities[choice]
    (0.87 vs 0.92, as in the frozen label a01; emenda A51)."""
    return {
        "model": model,
        "generationId": "gen-1",
        "answers": {
            "judge": {
                "type": "choice",
                "choice": choice,
                "probabilities": {
                    "proceed": probs[0],
                    "hold": probs[1],
                    "veto": probs[2],
                },
                "confidence": confidence,
            },
        },
        "providerMetadata": {"gateway": {"routing": {"resolvedProvider": provider}}},
    }


def start_stub(responder: Any) -> tuple[http.server.ThreadingHTTPServer, int, list[dict]]:
    """Start a stub endpoint on loopback port 0.

    ``responder(call_index, request) -> (status, body_dict_or_None)``
    decides each response. ``request`` carries ``path``, ``headers``
    and the decoded ``body``. Requests are recorded on the returned
    list, in order. Teardown: ``server.shutdown(); server_close()``."""
    requests: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass  # keep test output clean

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            entry = {
                "path": self.path,
                "headers": dict(self.headers),
                "body": json.loads(raw) if raw else None,
            }
            requests.append(entry)
            status, payload = responder(len(requests) - 1, entry)
            data = json.dumps(payload).encode("utf-8") if payload is not None else b""
            try:
                self.send_response(status)
                if payload is not None:
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if data:
                    self.wfile.write(data)
            except OSError:
                # Client already gave up (e.g. the C8 never-responds
                # test): the socket is gone, that is fine.
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1], requests


def _cfg(url: str, timeout_s: float = 15.0, key: str = "test-key") -> judge.JudgeConfig:
    return judge.JudgeConfig(
        model="typesafe-ai/jev", url=url, api_key_env="TEST_JUDGE_KEY",
        api_key_file="", timeout_s=timeout_s, api_key=key)


# ── spec 4.3: the question hash is pinned to the manifest ─────────────


def test_question_sha256_matches_manifest():
    """Editing JUDGE_QUESTION one character invalidates the frozen
    labels; this test says so (spec sections 4.3 and 7.3)."""
    manifest = load_manifest()
    assert judge.question_sha256() == manifest["judge_question_sha256"]


# ── spec 4.1: opt-in, defaults, key, url allowlist ────────────────────


def test_load_absent_block_returns_none(monkeypatch):
    monkeypatch.delenv("VERCEL_AI_GATEWAY_KEY", raising=False)
    assert judge.load() is None                   # conftest isolates config -> {}
    assert judge.load({}) is None
    assert judge.load({"council": {}}) is None     # a different block is not the judge


def test_load_empty_block_enables_defaults(monkeypatch):
    monkeypatch.delenv("VERCEL_AI_GATEWAY_KEY", raising=False)
    monkeypatch.setenv("VERCEL_AI_GATEWAY_KEY", "k-default")
    cfg = judge.load({"judge": {}})
    assert isinstance(cfg, judge.JudgeConfig)
    assert cfg.model == "typesafe-ai/jev"
    assert cfg.url == "https://ai-gateway.vercel.sh/v1/evaluate"
    assert cfg.api_key_env == "VERCEL_AI_GATEWAY_KEY"
    assert cfg.api_key_file == "~/.conscio-claude/vercel-gateway.env"
    assert cfg.timeout_s == 10.0
    assert cfg.api_key == "k-default"


def test_load_key_from_tilde_file(tmp_path, monkeypatch):
    """BUG-38: api_key_file goes through expanduser (spec section 4.1).

    The file is NOME=valor per line; only the line whose NOME matches
    api_key_env may yield the key (D2: no first-value fallback — a file
    holding only other secrets must not hand one of them out)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "judge-keys.env").write_text(
        "# dedicated gateway key file\n"
        "OTHER_NAME=other-value\n"
        "MY_GATEWAY_KEY=tok-123\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MY_GATEWAY_KEY", raising=False)
    cfg = judge.load({"judge": {
        "api_key_env": "MY_GATEWAY_KEY",
        "api_key_file": "~/judge-keys.env",
        "url": "http://127.0.0.1:9/v1/evaluate",
    }})
    assert isinstance(cfg, judge.JudgeConfig)
    assert cfg.api_key == "tok-123"


def test_load_key_file_without_name_match_is_no_key(tmp_path, monkeypatch):
    """D2: a key file holding only OTHER secrets is not a key source.
    load() reports no_key and the stub endpoint sees ZERO requests."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "judge-keys.env").write_text(
        "OTHER_TOKEN=secret-other\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MY_GATEWAY_KEY", raising=False)
    assert judge.load({"judge": {
        "api_key_env": "MY_GATEWAY_KEY",
        "api_key_file": "~/judge-keys.env",
        "url": "http://127.0.0.1:9/v1/evaluate",
    }}) == "no_key"
    server, port, requests = start_stub(lambda i, req: (200, _canonical_body()))
    try:
        # No config was ever built, so no request may exist:
        cfg = judge.load({"judge": {
            "api_key_env": "MY_GATEWAY_KEY",
            "api_key_file": "~/judge-keys.env",
            "url": f"http://127.0.0.1:{port}/v1/evaluate",
        }})
        assert cfg == "no_key"
        assert len(requests) == 0
    finally:
        server.shutdown()
        server.server_close()


def test_load_no_key(monkeypatch):
    monkeypatch.delenv("MISSING_JUDGE_KEY", raising=False)
    assert judge.load({"judge": {
        "api_key_env": "MISSING_JUDGE_KEY",
        "api_key_file": "/nonexistent/judge.env",
    }}) == "no_key"


def test_load_http_non_loopback_is_bad_config(monkeypatch):
    """http:// off-loopback is bad_config BEFORE any connection exists
    (spec section 4.1). socket.create_connection is patched to explode
    so any attempt to connect fails the test."""
    def _explode(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("a connection was attempted for a bad_config url")

    monkeypatch.setattr(socket, "create_connection", _explode)
    monkeypatch.setenv("VERCEL_AI_GATEWAY_KEY", "k")
    for url in ("http://example.com/v1/evaluate", "http://10.0.0.1/v1/evaluate",
                "ftp://127.0.0.1/x", "http://[::1]/x"):
        assert judge.load({"judge": {"url": url}}) == "bad_config", url


def test_load_http_loopback_is_not_bad_config(monkeypatch):
    """Loopback http passes the url allowlist (the test-server route);
    with no key available the outcome is no_key, not bad_config."""
    monkeypatch.delenv("SOME_MISSING_JUDGE_KEY", raising=False)
    assert judge.load({"judge": {
        "url": "http://localhost:8000/v1/evaluate",
        "api_key_env": "SOME_MISSING_JUDGE_KEY",
        "api_key_file": "/nonexistent/judge.env",
    }}) == "no_key"


# ── spec 4.4 / 8: C2, C8, C8b over the stub endpoint ──────────────────


def test_ask_canonical_verdict_c2():
    server, port, requests = start_stub(lambda i, req: (200, _canonical_body()))
    try:
        cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate")
        verdict = judge.ask(cfg, "push to main?", "CI green", ["push", "wait"])
        assert isinstance(verdict, judge.JudgeVerdict)
        assert verdict.choice == "veto" == max(
            verdict.probabilities, key=verdict.probabilities.get)
        assert verdict.probabilities == {"proceed": 0.05, "hold": 0.03, "veto": 0.92}
        # Emenda A51: confidence is the model-reported value, NOT
        # probabilities[choice] — the guard below would fail if the
        # parser ever fell back to the chosen class' probability.
        assert verdict.confidence == 0.87
        assert verdict.confidence != verdict.probabilities[verdict.choice]
        assert verdict.model == "typesafe-ai/jev"
        assert verdict.provider == "jev-canonical"
        assert len(requests) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_ask_never_responds_times_out_c8():
    """A server that accepts and never answers: with timeout_s=1 the
    call must return within 1.5 s (spec section 8, row C8)."""
    def responder(i: int, req: dict) -> tuple[int, None]:
        time.sleep(8)  # outlive the client deadline; handler thread is a daemon
        return 200, None

    server, port, _ = start_stub(responder)
    try:
        cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate", timeout_s=1.0)
        t0 = time.monotonic()
        result = judge.ask(cfg, "q", "c")
        elapsed = time.monotonic() - t0
        assert result == "timeout"
        assert elapsed <= 1.5, f"took {elapsed:.2f}s"
    finally:
        server.shutdown()
        server.server_close()


def test_ask_retries_429_529_c8b():
    """429, 429, 200 => verdict after exactly 3 requests (R10)."""
    server, port, requests = start_stub(
        lambda i, req: (429, None) if i < 2 else (200, _canonical_body()))
    try:
        cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate", timeout_s=15.0)
        result = judge.ask(cfg, "q", "c")
        assert isinstance(result, judge.JudgeVerdict)
        assert len(requests) == 3
    finally:
        server.shutdown()
        server.server_close()


def test_ask_401_single_request_c8b():
    """Any other 4xx fails immediately: exactly 1 request, no retry."""
    server, port, requests = start_stub(lambda i, req: (401, None))
    try:
        cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate")
        assert judge.ask(cfg, "q", "c") == "http_401"
        assert len(requests) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_ask_network_error():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                              http.server.BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.server_close()  # the port is now refused
    cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate")
    assert judge.ask(cfg, "q", "c") == "network"


# ── spec 4.2: what leaves the machine ─────────────────────────────────


def test_ask_body_state_exact():
    """The recorded body carries exactly question/context/options —
    options only when present. Never engine state, instance id, path,
    agent name, or relay content (spec section 4.2)."""
    server, port, requests = start_stub(lambda i, req: (200, _canonical_body()))
    try:
        cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate")
        judge.ask(cfg, "ship it?", "CI green", ["ship", "hold"])
        with_opts = requests[0]["body"]
        assert set(with_opts["state"]) == {"question", "context", "options"}
        assert with_opts["state"]["question"] == "ship it?"
        assert with_opts["state"]["context"] == "CI green"
        assert with_opts["state"]["options"] == ["ship", "hold"]

        judge.ask(cfg, "ship it?", "CI green")
        without_opts = requests[1]["body"]
        assert set(without_opts["state"]) == {"question", "context"}

        for body in requests:
            top_keys = set(body["body"].keys())
            assert not (ForbiddenKeys & top_keys)
            assert not (ForbiddenKeys & set(body["body"]["state"].keys()))
    finally:
        server.shutdown()
        server.server_close()


# ── spec 4.3: malformed responses never proceed ───────────────────────


@pytest.mark.parametrize("body, why", [
    ({"model": "jev"}, "answers missing"),
    ({"answers": {"j": {"choice": "maybe",
                        "probabilities": {"proceed": 0.1, "hold": 0.2, "veto": 0.7},
                        "confidence": 0.5}}},
     "choice outside proceed/hold/veto"),
    ({"answers": {"j": {"choice": "veto",
                        "probabilities": {"proceed": 0.1, "hold": 0.9},
                        "confidence": 0.9}}},
     "probabilities missing the veto key"),
    ({"answers": {"j": {"type": "choice", "choice": "veto",
                        "probabilities": {"proceed": 0.05, "hold": 0.03, "veto": 0.92}}}},
     "confidence missing (emenda A51: absence is a broken contract)"),
    ({"answers": {"j": {"type": "choice", "choice": "veto",
                        "probabilities": {"proceed": 0.05, "hold": 0.03, "veto": 0.92},
                        "confidence": float("nan")}}},
     "confidence NaN (json.loads accepts it, it must still fail)"),
    ({"answers": {"j": {"type": "choice", "choice": "veto",
                        "probabilities": {"proceed": 0.05, "hold": float("inf"),
                                          "veto": 0.92},
                        "confidence": 0.87}}},
     "probability Infinity (json.loads accepts it, it must still fail)"),
    ({"answers": {"j": {"type": "choice", "choice": "veto",
                        "probabilities": {"proceed": 0.05, "hold": 0.03, "veto": 0.92},
                        "confidence": True}}},
     "confidence bool is not numeric"),
])
def test_ask_malformed_never_proceeds(body, why):
    server, port, requests = start_stub(lambda i, req: (200, body))
    try:
        cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate")
        assert judge.ask(cfg, "q", "c") == "malformed", why
        assert len(requests) == 1, why
    finally:
        server.shutdown()
        server.server_close()


def test_ask_malformed_json_response():
    server, port, _ = start_stub(lambda i, req: (200, {"answers": {"j": "junk"}}))
    try:
        cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate")
        assert judge.ask(cfg, "q", "c") == "malformed"
    finally:
        server.shutdown()
        server.server_close()
