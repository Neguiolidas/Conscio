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
import urllib.error
import urllib.request
from typing import Any

import pytest
from council_bench import load_manifest

from conscio import judge

# Forbidden keys at the state level and beyond the envelope's own top
# keys (emenda A56 made "model" a legitimate top-level envelope key, so
# it is no longer in this set).
ForbiddenKeys = {"instance", "engine", "path", "agent", "relay", "identity",
                 "workspace"}


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


def _envelope_error(body: Any, expect_model: str | None) -> str | None:
    """The stub's server side of the emenda-A56 envelope contract: the
    live API rejects anything that is not exactly
    ``{"model", "state", "questions"}`` at the root, with a single-key
    ``questions`` whose value is ``JUDGE_QUESTION`` and ``model`` equal
    to the cfg's model (when ``expect_model`` is given). Any violation
    is refused with 400 — this is what made the T5 defect (the loose
    question at root, no model, no questions) a visible ``http_400``
    instead of a silent pass."""
    if not isinstance(body, dict) or set(body) != {"model", "state", "questions"}:
        return "top-level keys are not exactly {model, state, questions}"
    questions = body["questions"]
    if not isinstance(questions, dict) or len(questions) != 1:
        return "questions must be a dict with a single key"
    if next(iter(questions.values())) != judge.JUDGE_QUESTION:
        return "the single questions value is not JUDGE_QUESTION"
    if expect_model is not None and body["model"] != expect_model:
        return "model does not reach the body as the cfg's model"
    return None


def start_stub(responder: Any, expect_model: str | None = None
               ) -> tuple[http.server.ThreadingHTTPServer, int, list[dict]]:
    """Start a stub endpoint on loopback port 0.

    ``responder(call_index, request) -> (status, body_dict_or_None)``
    decides each response. ``request`` carries ``path``, ``headers``
    and the decoded ``body``. Requests are recorded on the returned
    list, in order. Before the responder runs, the body is held to the
    envelope contract (``_envelope_error``): a violating body is
    refused with 400 and never reaches the responder. Teardown:
    ``server.shutdown(); server_close()``."""
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
            err = _envelope_error(entry["body"], expect_model)
            if err is not None:
                data = json.dumps({"error": err}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
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


def _cfg(url: str, timeout_s: float = 15.0, key: str = "test-key",
         model: str = "typesafe-ai/jev") -> judge.JudgeConfig:
    return judge.JudgeConfig(
        model=model, url=url, api_key_env="TEST_JUDGE_KEY",
        api_key_file="", timeout_s=timeout_s, api_key=key)


# ── spec 4.3: the question hash is pinned to the manifest ─────────────


def test_question_sha256_matches_manifest():
    """Editing JUDGE_QUESTION one character invalidates the frozen
    labels; this test says so (spec sections 4.3 and 7.3)."""
    manifest = load_manifest()
    assert judge.question_sha256() == manifest["judge_question_sha256"]


# ── spec 4.1: opt-in, defaults, key, url allowlist ────────────────────


def test_load_absent_block_returns_none(monkeypatch):
    monkeypatch.delenv("EXPERIENTIAL_API_KEY", raising=False)
    assert judge.load() is None                   # conftest isolates config -> {}
    assert judge.load({}) is None
    assert judge.load({"council": {}}) is None     # a different block is not the judge


def test_load_empty_block_enables_defaults(monkeypatch):
    """Emenda A56 defaults: the Experiential direct API (the Vercel
    gateway route was dropped 2026-09-27)."""
    monkeypatch.delenv("EXPERIENTIAL_API_KEY", raising=False)
    monkeypatch.setenv("EXPERIENTIAL_API_KEY", "k-default")
    cfg = judge.load({"judge": {}})
    assert isinstance(cfg, judge.JudgeConfig)
    assert cfg.model == "jev-latest"
    assert cfg.url == "https://api.experientiallabs.ai/v1/systemone"
    assert cfg.api_key_env == "EXPERIENTIAL_API_KEY"
    assert cfg.api_key_file == "~/.conscio-claude/experiential.env"
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
    monkeypatch.setenv("EXPERIENTIAL_API_KEY", "k")
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
    server, port, requests = start_stub(
        lambda i, req: (200, _canonical_body()), expect_model="typesafe-ai/jev")
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
    """The recorded body carries exactly question/context/options in
    ``state`` — options only when present — inside the emenda-A56
    envelope (top-level keys exactly model/state/questions). Never
    engine state, instance id, path, agent name, or relay content
    (spec section 4.2; the envelope holds, now within the envelope)."""
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

        for req in requests:
            body = req["body"]
            assert set(body) == {"model", "state", "questions"}
            assert not (ForbiddenKeys & set(body["state"]))
    finally:
        server.shutdown()
        server.server_close()


# ── emenda A56: the request envelope (spec section 4.3) ───────────────


def test_ask_model_reaches_the_body():
    """The cfg's model is what the server sees (emenda A56): a cfg
    with model 'x-test' produces model 'x-test' in the body — the stub
    refuses any other value with 400, so a fixed-model or dropped-
    model payload would surface as http_400 here."""
    server, port, requests = start_stub(
        lambda i, req: (200, _canonical_body()), expect_model="x-test")
    try:
        cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate", model="x-test")
        verdict = judge.ask(cfg, "q", "c")
        assert isinstance(verdict, judge.JudgeVerdict)
        assert requests[0]["body"]["model"] == "x-test"
    finally:
        server.shutdown()
        server.server_close()


def test_stub_rejects_the_old_loose_body():
    """The T5 defect, pinned against the stub itself: the old loose
    body (question fields at root, no model, no questions) is exactly
    what every real call sent before A56 — and exactly what the
    server now refuses with 400. Sent straight with urllib, no
    judge.ask involved, so this test pins the stub's contract even
    while the module is correct."""
    old_body = {
        "type": judge.JUDGE_QUESTION["type"],
        "instructions": judge.JUDGE_QUESTION["instructions"],
        "criteria": judge.JUDGE_QUESTION["criteria"],
        "state": {"question": "q", "context": "c"},
    }
    server, port, _ = start_stub(lambda i, req: (200, _canonical_body()))
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/evaluate",
            data=json.dumps(old_body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(request, timeout=5)
        assert excinfo.value.code == 400
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("top, nested, expected, why", [
    ("typesafe", "vercel-x", "typesafe",
     "both present: the top-level str wins"),
    (None, "vercel-x", "vercel-x",
     "nested only: Vercel routing falls through to provider"),
    (42, "vercel-x", "vercel-x",
     "wrong-type top (int): falls back to nested, never malformed"),
    ({"p": 1}, "vercel-x", "vercel-x",
     "wrong-type top (dict): falls back to nested"),
    (None, None, "", "neither present: provider is ''"),
    (42, None, "", "wrong-type top, no nested: ''"),
], ids=["both-top-wins", "nested-only", "top-int", "top-dict",
        "neither", "top-int-no-nested"])
def test_provider_precedence(top, nested, expected, why):
    """Emenda A56 precedence: top-level "provider" (Experiential)
    beats providerMetadata.gateway.routing.resolvedProvider (Vercel);
    a wrong type at any level degrades instead of breaking the
    verdict (spec section 4.4)."""
    body = _canonical_body()
    body["providerMetadata"] = (
        {"gateway": {"routing": {"resolvedProvider": nested}}}
        if nested is not None else {})
    if top is not None:
        body["provider"] = top
    server, port, requests = start_stub(lambda i, req: (200, body))
    try:
        cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate")
        verdict = judge.ask(cfg, "q", "c")
        assert isinstance(verdict, judge.JudgeVerdict), why
        assert verdict.provider == expected, (why, verdict.provider)
        assert len(requests) == 1
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


# ── A53: the "ask never raises" contract (spec section 4.4) ──────────
# Three historically-leaked paths (P1-P3) plus a deterministic fuzz of
# the two parsing surfaces. The fuzz tests collect violations and
# assert zero with the count in the message, so a teeth-mutant shows
# exactly how many mutations escape the contract.

import copy
import math
import random

_JUNK_VALUES = ["junk", 12345, 10 ** 399, float("nan"), float("inf"),
                float("-inf"), True, False, None, [1, 2], {"n": 1}]


def test_p1_non_utf8_key_file_is_no_key(tmp_path, monkeypatch):
    """P1 regression: a key file that is not UTF-8 is unreadable ->
    no key -> 'no_key' (spec section 4.1: sem chave => no_key), never
    UnicodeDecodeError. The stub sees ZERO requests."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "judge-keys.env").write_bytes(b"MY_GATEWAY_KEY=\xff\xfe")
    monkeypatch.delenv("MY_GATEWAY_KEY", raising=False)
    assert judge.load({"judge": {
        "api_key_env": "MY_GATEWAY_KEY",
        "api_key_file": "~/judge-keys.env",
        "url": "http://127.0.0.1:9/v1/evaluate",
    }}) == "no_key"
    server, port, requests = start_stub(lambda i, req: (200, _canonical_body()))
    try:
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


@pytest.mark.parametrize("bad_meta", [
    "x",
    {"gateway": "y"},
    {"gateway": {"routing": 5}},
], ids=["str", "gateway-str", "routing-int"])
def test_p2_wrong_typed_provider_metadata_yields_empty_provider(bad_meta):
    """P2 regression: wrong-typed providerMetadata degrades to
    provider == "" (it is optional metadata, like a missing
    resolvedProvider) — never AttributeError, never 'malformed'."""
    server, port, requests = start_stub(
        lambda i, req: (200, dict(_canonical_body(), providerMetadata=bad_meta)))
    try:
        cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate")
        verdict = judge.ask(cfg, "q", "c")
        assert isinstance(verdict, judge.JudgeVerdict)
        assert verdict.provider == ""
        assert verdict.confidence == 0.87
        assert len(requests) == 1
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("where", ["confidence", "probability"])
def test_p3_huge_json_int_is_malformed(where):
    """P3 regression: a 400-digit JSON int literal is not a finite
    number -> 'malformed', never OverflowError (emenda A51 + F3)."""
    body = _canonical_body()
    entry = body["answers"]["judge"]
    if where == "confidence":
        entry["confidence"] = 10 ** 399
    else:
        entry["probabilities"]["veto"] = 10 ** 399
    server, port, requests = start_stub(lambda i, req: (200, body))
    try:
        cfg = _cfg(f"http://127.0.0.1:{port}/v1/evaluate")
        assert judge.ask(cfg, "q", "c") == "malformed"
        assert len(requests) == 1
    finally:
        server.shutdown()
        server.server_close()


def _value_paths(node: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Paths to every dict/list *entry value* at every depth (the
    value of providerMetadata itself, of gateway, of routing, of the
    probability keys — not just the deepest leaves)."""
    paths: list[tuple[Any, ...]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            p = prefix + (key,)
            paths.append(p)
            paths.extend(_value_paths(value, p))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            p = prefix + (index,)
            paths.append(p)
            paths.extend(_value_paths(value, p))
    return paths


def test_fuzz_parse_verdict_contract():
    """A53 fuzz (>=2000 deterministic mutations, fixed seed, no new
    dependencies): every mutated canonical response must make
    _parse_verdict return JudgeVerdict or None WITHOUT raising; a
    returned verdict must have choice in CHOICES and finite float
    confidence/probabilities. Mutations target any dict/list entry
    value at any depth (replace with junk / delete the key / nest the
    value one level deeper), so intermediate nodes like
    providerMetadata/gateway/routing get non-dict values too."""
    rng = random.Random(20260926)
    base = _canonical_body()
    total = 0
    violations: list[tuple[int, str]] = []
    for i in range(2000):
        data = copy.deepcopy(base)
        op = i % 3
        paths = _value_paths(data)
        if op == 0 and paths:  # replace any entry value with junk
            path = rng.choice(paths)
            node = data
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = rng.choice(_JUNK_VALUES)
        elif op == 1:  # delete a random dict key at any depth
            dict_paths = [p for p in paths if isinstance(p[-1], str)]
            if dict_paths:
                path = rng.choice(dict_paths)
                node = data
                for key in path[:-1]:
                    node = node[key]
                del node[path[-1]]
        elif op == 2 and paths:  # nest an entry value one level deeper
            path = rng.choice(paths)
            node = data
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = {"nested": node[path[-1]]}
        total += 1
        try:
            result = judge._parse_verdict(data)
        except Exception as exc:
            violations.append((i, f"raised {type(exc).__name__}: {exc}"))
            continue
        if result is not None:
            fields_ok = (
                result.choice in judge.CHOICES
                and isinstance(result.confidence, float)
                and math.isfinite(result.confidence)
                and set(result.probabilities) == set(judge.CHOICES)
                and all(isinstance(v, float) and math.isfinite(v)
                        for v in result.probabilities.values()))
            if not fields_ok:
                violations.append((i, f"bad verdict fields: {result!r}"))
    assert not violations, (
        f"{len(violations)}/{total} parse fuzz mutations violated the contract; "
        f"first: {violations[:5]}")


def test_fuzz_load_contract(tmp_path):
    """A53 fuzz (>=500 random configs, fixed seed): every random 'judge'
    block (each field drawn from a random type) must make load()
    return JudgeConfig | None | str WITHOUT raising; a JudgeConfig
    always carries a key; str outcomes are no_key/bad_config only."""
    rng = random.Random(9531)
    key_file = str(tmp_path / "nonexistent-judge.env")
    pools = {
        "model": ["junk-model", 5, True, None, [], {"m": 1}],
        "url": ["https://judge.example/v1/evaluate", "junk-str", 7,
                float("nan"), ["u"], {"u": 1}],
        "api_key_env": ["JUDGE_FUZZ_KEY_NOT_SET", "junk", 3, False, None, {"a": 1}],
        "api_key_file": [key_file, "junk-relpath", 9, 10 ** 399, None, []],
        "timeout_s": [5, -1, 0, float("nan"), "junk", 10 ** 399, True,
                      None, [], {"t": 1}, 10.5],
    }
    total = 0
    violations: list[tuple[int, str]] = []
    for i in range(500):
        if i % 17 == 0:  # sometimes a non-dict top level or no block
            cfg = rng.choice(["junk", 5, [1], None, {"council": {}}])
        else:
            block: dict[str, Any] = {}
            for field, pool in pools.items():
                if rng.random() < 0.7:  # field present with random type
                    block[field] = rng.choice(pool)
            if i % 23 == 0:  # sometimes a non-dict block
                block = rng.choice(["junk", 5, [1]])
            cfg = {"judge": block}
        total += 1
        try:
            outcome = judge.load(cfg)
        except Exception as exc:
            violations.append((i, f"load raised {type(exc).__name__}: {exc} (cfg={cfg!r})"))
            continue
        ok = outcome is None or isinstance(outcome, (judge.JudgeConfig, str))
        if isinstance(outcome, judge.JudgeConfig) and not outcome.api_key:
            ok = False
        if isinstance(outcome, str) and outcome not in ("no_key", "bad_config"):
            ok = False
        if not ok:
            violations.append((i, f"bad outcome {outcome!r} for cfg={cfg!r}"))
    assert not violations, (
        f"{len(violations)}/{total} load fuzz configs violated the contract; "
        f"first: {violations[:5]}")
