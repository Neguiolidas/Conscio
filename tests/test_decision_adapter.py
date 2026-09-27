# tests/test_decision_adapter.py
"""Tests for DecisionAdapter and POST /v1/systemone contract (v4.8 S3).

Covers:
- Loader & presets (experiential, typesafe, systemone), validations, and error codes
- Local validation before I/O (raising ValueError)
- decide() against a loopback stub HTTP server: success, retries (429, 529), no-retry (401),
  network vs timeout, all malformed response variants
- Fuzzing with deterministic seed (>=2000 mutations) asserting only DecisionError
- Hub validate and redact tests
"""
from __future__ import annotations

import copy
import http.server
import json
import random
import threading
import time
import traceback
import urllib.error
import urllib.request
from typing import Any

import pytest

from conscio.decision_adapter import (
    DEFAULT_TIMEOUT_S,
    Answer,
    Decision,
    DecisionAdapter,
    DecisionError,
    load_decision_adapter,
)
from conscio.hub import config as hub_config

# ── Test helpers and stub server ─────────────────────────────────────────────

def _canonical_choice_response(
    choice: str = "proceed",
    probs: dict[str, float] | None = None,
    confidence: float = 0.95,
    model: str = "jev-latest",
) -> dict[str, Any]:
    return {
        "model": model,
        "answers": {
            "q_choice": {
                "type": "choice",
                "choice": choice,
                "probabilities": probs or {"proceed": 0.95, "hold": 0.03, "veto": 0.02},
                "confidence": confidence,
            }
        },
    }


def start_stub(responder: Any) -> tuple[http.server.ThreadingHTTPServer, int, list[dict]]:
    """Start a loopback HTTP stub server on an ephemeral port."""
    requests: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            body = None
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = raw
            entry = {
                "path": self.path,
                "headers": dict(self.headers),
                "body": body,
                "raw": raw,
            }
            requests.append(entry)
            status, payload, raw_bytes = responder(len(requests) - 1, entry)
            self.send_response(status)
            if raw_bytes is not None:
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw_bytes)))
                self.end_headers()
                self.wfile.write(raw_bytes)
            elif payload is not None:
                data = json.dumps(payload).encode("utf-8")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_header("Content-Length", "0")
                self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1], requests


# ── Loader tests ─────────────────────────────────────────────────────────────

def test_loader_absent_block_returns_none():
    assert load_decision_adapter({}) is None
    assert load_decision_adapter({"adapter": {"type": "openai"}}) is None
    assert load_decision_adapter({"decision_adapter": None}) is None


def test_loader_bad_block_type_returns_bad_config():
    assert load_decision_adapter({"decision_adapter": "not-a-dict"}) == "bad_config"
    assert load_decision_adapter({"decision_adapter": [1, 2]}) == "bad_config"


def test_loader_inline_api_key_forbidden():
    cfg = {"decision_adapter": {"type": "experiential", "api_key": "raw-key"}}
    assert load_decision_adapter(cfg) == "bad_config"


def test_loader_unknown_key_returns_bad_config():
    cfg = {"decision_adapter": {"type": "experiential", "unknown_key": "val"}}
    assert load_decision_adapter(cfg) == "bad_config"


def test_loader_invalid_type_returns_bad_config():
    assert load_decision_adapter({"decision_adapter": {"type": "bogus"}}) == "bad_config"
    assert load_decision_adapter({"decision_adapter": {"type": 123}}) == "bad_config"


def test_loader_presets_defaults(monkeypatch):
    monkeypatch.setenv("EXPERIENTIAL_API_KEY", "exp-key-123")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-key-456")

    # experiential preset
    ad_exp = load_decision_adapter({"decision_adapter": {"type": "experiential"}})
    assert isinstance(ad_exp, DecisionAdapter)
    assert ad_exp.type == "experiential"
    assert ad_exp.base_url == "https://api.experientiallabs.ai"
    assert ad_exp.model == "jev-latest"
    assert ad_exp.api_key_env == "EXPERIENTIAL_API_KEY"
    assert ad_exp.api_key == "exp-key-123"
    assert ad_exp.timeout_s == DEFAULT_TIMEOUT_S

    # typesafe preset
    ad_ts = load_decision_adapter({"decision_adapter": {"type": "typesafe"}})
    assert isinstance(ad_ts, DecisionAdapter)
    assert ad_ts.type == "typesafe"
    assert ad_ts.base_url == "https://api.typesafe.ai"
    assert ad_ts.model == "jev-latest"
    assert ad_ts.api_key_env == "TYPESAFE_API_KEY"
    assert ad_ts.api_key == "typesafe-key-456"

    # repr does not expose the key
    assert "exp-key-123" not in repr(ad_exp)
    assert "typesafe-key-456" not in repr(ad_ts)


def test_loader_systemone_requires_base_url_and_api_key_env(monkeypatch):
    monkeypatch.setenv("CUSTOM_KEY", "custom-secret")

    # missing base_url
    cfg1 = {"decision_adapter": {"type": "systemone", "api_key_env": "CUSTOM_KEY"}}
    assert load_decision_adapter(cfg1) == "bad_config"

    # missing api_key_env
    cfg2 = {"decision_adapter": {"type": "systemone", "base_url": "https://custom.endpoint.com"}}
    assert load_decision_adapter(cfg2) == "bad_config"

    # valid systemone
    cfg3 = {
        "decision_adapter": {
            "type": "systemone",
            "base_url": "https://custom.endpoint.com",
            "api_key_env": "CUSTOM_KEY",
        }
    }
    ad = load_decision_adapter(cfg3)
    assert isinstance(ad, DecisionAdapter)
    assert ad.type == "systemone"
    assert ad.base_url == "https://custom.endpoint.com"
    assert ad.api_key_env == "CUSTOM_KEY"
    assert ad.api_key == "custom-secret"
    assert ad.model == "jev-latest"


def test_loader_timeout_validation(monkeypatch):
    monkeypatch.setenv("EXPERIENTIAL_API_KEY", "k")

    for bad_t in (0, -1, -5.5, float("nan"), float("inf"), float("-inf"), 1e400, True, False, "10"):
        cfg = {"decision_adapter": {"type": "experiential", "timeout_s": bad_t}}
        assert load_decision_adapter(cfg) == "bad_config"

    # finite valid timeout
    cfg_ok = {"decision_adapter": {"type": "experiential", "timeout_s": 25.5}}
    ad = load_decision_adapter(cfg_ok)
    assert isinstance(ad, DecisionAdapter)
    assert ad.timeout_s == 25.5


def test_loader_base_url_validation(monkeypatch):
    monkeypatch.setenv("EXPERIENTIAL_API_KEY", "k")

    # http outside loopback is rejected
    assert load_decision_adapter(
        {"decision_adapter": {"type": "experiential", "base_url": "http://api.external.com"}}
    ) == "bad_config"

    # ftp / file / non-http(s) rejected
    assert load_decision_adapter(
        {"decision_adapter": {"type": "experiential", "base_url": "file:///etc/passwd"}}
    ) == "bad_config"

    # embedded credentials rejected
    assert load_decision_adapter(
        {"decision_adapter": {"type": "experiential", "base_url": "https://user:pass@api.external.com"}}
    ) == "bad_config"

    # http on 127.0.0.1 or localhost is allowed
    ad1 = load_decision_adapter(
        {"decision_adapter": {"type": "experiential", "base_url": "http://127.0.0.1:8080/v1/"}}
    )
    assert isinstance(ad1, DecisionAdapter)
    assert ad1.base_url == "http://127.0.0.1:8080/v1"  # rstrip('/')

    ad2 = load_decision_adapter(
        {"decision_adapter": {"type": "experiential", "base_url": "http://localhost:5000"}}
    )
    assert isinstance(ad2, DecisionAdapter)
    assert ad2.base_url == "http://localhost:5000"


def test_loader_key_resolution_order_and_file(tmp_path, monkeypatch):
    # Order: env -> vault -> file
    key_file = tmp_path / "keys.env"
    key_file.write_text("TEST_KEY=file-key\nOTHER_KEY=other-val\n")

    monkeypatch.setenv("CONSCIO_VAULT_DIR", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir()
    (tmp_path / "vault" / "TEST_KEY").write_text("vault-key\n")

    cfg = {
        "decision_adapter": {
            "type": "systemone",
            "base_url": "https://example.com",
            "api_key_env": "TEST_KEY",
            "api_key_file": str(key_file),
        }
    }

    # 1. Env present wins over vault and file
    monkeypatch.setenv("TEST_KEY", "env-key")
    ad = load_decision_adapter(cfg)
    assert isinstance(ad, DecisionAdapter)
    assert ad.api_key == "env-key"

    # 2. Env absent, vault wins over file
    monkeypatch.delenv("TEST_KEY", raising=False)
    ad = load_decision_adapter(cfg)
    assert isinstance(ad, DecisionAdapter)
    assert ad.api_key == "vault-key"

    # 3. Env absent, vault absent, file wins
    monkeypatch.delenv("TEST_KEY", raising=False)
    (tmp_path / "vault" / "TEST_KEY").unlink()
    ad = load_decision_adapter(cfg)
    assert isinstance(ad, DecisionAdapter)
    assert ad.api_key == "file-key"

    # 4. File holding only OTHER keys -> no_key
    cfg_other = {
        "decision_adapter": {
            "type": "systemone",
            "base_url": "https://example.com",
            "api_key_env": "NON_EXISTENT_KEY",
            "api_key_file": str(key_file),
        }
    }
    assert load_decision_adapter(cfg_other) == "no_key"

    # 5. Tilde expansion in api_key_file
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "my_key.env").write_text("TEST_KEY=expanded-home-key\n")
    monkeypatch.setenv("HOME", str(fake_home))
    cfg_tilde = {
        "decision_adapter": {
            "type": "systemone",
            "base_url": "https://example.com",
            "api_key_env": "TEST_KEY",
            "api_key_file": "~/my_key.env",
        }
    }
    ad_tilde = load_decision_adapter(cfg_tilde)
    assert isinstance(ad_tilde, DecisionAdapter)
    assert ad_tilde.api_key == "expanded-home-key"


# ── Local validation tests ───────────────────────────────────────────────────

def test_local_validation_boolean_rejected_with_noul_message():
    ad = DecisionAdapter("experiential", "https://api.test", "m", "K", None, 10.0, "k")
    with pytest.raises(ValueError) as exc:
        ad.decide("state", {"q1": {"type": "boolean"}})
    assert "use 'noul' instead" in str(exc.value)


def test_local_validation_empty_or_invalid_questions():
    ad = DecisionAdapter("experiential", "https://api.test", "m", "K", None, 10.0, "k")
    with pytest.raises(ValueError):
        ad.decide("state", {})
    with pytest.raises(ValueError):
        ad.decide("state", "not-a-dict")  # type: ignore
    with pytest.raises(ValueError):
        ad.decide("state", {"": {"type": "noul"}})
    with pytest.raises(ValueError):
        ad.decide("state", {"q1": "not-a-dict"})  # type: ignore
    with pytest.raises(ValueError):
        ad.decide("state", {"q1": {"type": "unsupported"}})


def test_local_validation_choice_criteria():
    ad = DecisionAdapter("experiential", "https://api.test", "m", "K", None, 10.0, "k")
    # missing criteria
    with pytest.raises(ValueError):
        ad.decide("state", {"q1": {"type": "choice"}})
    # empty criteria (0 options)
    with pytest.raises(ValueError):
        ad.decide("state", {"q1": {"type": "choice", "criteria": {}}})
    # 256 options (> 255)
    crit_256 = {f"opt_{i}": f"desc_{i}" for i in range(256)}
    with pytest.raises(ValueError):
        ad.decide("state", {"q1": {"type": "choice", "criteria": crit_256}})


def test_local_validation_score_criteria():
    ad = DecisionAdapter("experiential", "https://api.test", "m", "K", None, 10.0, "k")
    # missing criteria
    with pytest.raises(ValueError):
        ad.decide("state", {"q1": {"type": "score"}})
    # 0 levels
    with pytest.raises(ValueError):
        ad.decide("state", {"q1": {"type": "score", "criteria": {}}})
    # 11 levels (> 10)
    crit_11 = {f"{i}": f"level {i}" for i in range(11)}
    with pytest.raises(ValueError):
        ad.decide("state", {"q1": {"type": "score", "criteria": crit_11}})


def test_local_validation_does_zero_network_requests():
    server, port, requests = start_stub(lambda i, r: (200, {}, None))
    try:
        ad = DecisionAdapter("systemone", f"http://127.0.0.1:{port}", "m", "K", None, 10.0, "k")
        with pytest.raises(ValueError):
            ad.decide("state", {"q1": {"type": "boolean"}})
        with pytest.raises(ValueError):
            ad.decide("state", {"q1": {"type": "choice"}})  # no criteria
        assert len(requests) == 0
    finally:
        server.shutdown()
        server.server_close()


# ── decide() transport and response tests ─────────────────────────────────────

def test_decide_choice_success():
    resp_body = {
        "model": "jev-v1",
        "answers": {
            "q_proceed": {
                "type": "choice",
                "choice": "proceed",
                "probabilities": {"proceed": 0.85, "hold": 0.10, "veto": 0.05},
                "confidence": 0.88,
            }
        },
    }
    server, port, requests = start_stub(lambda i, r: (200, resp_body, None))
    try:
        ad = DecisionAdapter("systemone", f"http://127.0.0.1:{port}", "test-model", "K", None, 10.0, "secret-bearer")
        questions = {
            "q_proceed": {
                "type": "choice",
                "criteria": {"proceed": "Go", "hold": "Wait", "veto": "Stop"},
            }
        }
        dec = ad.decide({"ctx": 123}, questions)
        assert isinstance(dec, Decision)
        assert dec.model == "jev-v1"
        assert "q_proceed" in dec.answers
        ans = dec.answers["q_proceed"]
        assert isinstance(ans, Answer)
        assert ans.type == "choice"
        assert ans.value == "proceed"
        assert ans.probabilities == {"proceed": 0.85, "hold": 0.10, "veto": 0.05}
        assert ans.confidence == 0.88

        # Verify request headers and path
        assert len(requests) == 1
        req = requests[0]
        assert req["path"] == "/v1/systemone"
        assert req["headers"]["Authorization"] == "Bearer secret-bearer"
        assert req["headers"]["Content-Type"] == "application/json"
        assert req["body"]["model"] == "test-model"
        assert req["body"]["questions"] == questions
        assert req["body"]["state"] == {"ctx": 123}
    finally:
        server.shutdown()
        server.server_close()


def test_decide_noul_success():
    resp_body = {
        "model": "jev-v1",
        "answers": {
            "q_prob": {
                "type": "noul",
                "noul": 0.72,
            }
        },
    }
    server, port, _requests = start_stub(lambda i, r: (200, resp_body, None))
    try:
        ad = DecisionAdapter("systemone", f"http://127.0.0.1:{port}", "m", "K", None, 10.0, "k")
        dec = ad.decide({}, {"q_prob": {"type": "noul"}})
        assert dec.answers["q_prob"] == Answer(
            type="noul",
            value=0.72,
            probabilities={},
            confidence=None,
        )
    finally:
        server.shutdown()
        server.server_close()


def test_decide_score_success():
    resp_body = {
        "model": "jev-v1",
        "answers": {
            "q_score": {
                "type": "score",
                "score": 3.0,
                "probabilities": {"1": 0.05, "2": 0.15, "3": 0.80},
                "confidence": 0.91,
            }
        },
    }
    server, port, _requests = start_stub(lambda i, r: (200, resp_body, None))
    try:
        ad = DecisionAdapter("systemone", f"http://127.0.0.1:{port}", "m", "K", None, 10.0, "k")
        dec = ad.decide(
            {},
            {"q_score": {"type": "score", "criteria": {"1": "Low", "2": "Med", "3": "High"}}},
        )
        assert dec.answers["q_score"] == Answer(
            type="score",
            value=3.0,
            probabilities={"1": 0.05, "2": 0.15, "3": 0.80},
            confidence=0.91,
        )
    finally:
        server.shutdown()
        server.server_close()


def test_decide_retries_429_and_529_then_succeeds(monkeypatch):
    # Monkeypatch time.sleep to avoid waiting during test
    monkeypatch.setattr(time, "sleep", lambda s: None)

    # 429 on call 0, 529 on call 1, 200 on call 2
    def responder(call_idx: int, req: dict):
        if call_idx == 0:
            return 429, {"error": "rate limit"}, None
        if call_idx == 1:
            return 529, {"error": "overloaded"}, None
        return 200, _canonical_choice_response(), None

    server, port, requests = start_stub(responder)
    try:
        ad = DecisionAdapter("systemone", f"http://127.0.0.1:{port}", "m", "K", None, 10.0, "k")
        dec = ad.decide({}, {"q_choice": {"type": "choice", "criteria": {"proceed": "", "hold": "", "veto": ""}}})
        assert isinstance(dec, Decision)
        assert len(requests) == 3
    finally:
        server.shutdown()
        server.server_close()


def test_decide_401_fails_immediately_without_retry():
    def responder(call_idx: int, req: dict):
        return 401, {"error": "unauthorized"}, None

    server, port, requests = start_stub(responder)
    try:
        ad = DecisionAdapter("systemone", f"http://127.0.0.1:{port}", "m", "K", None, 10.0, "k")
        with pytest.raises(DecisionError) as exc:
            ad.decide({}, {"q_choice": {"type": "choice", "criteria": {"proceed": "", "hold": "", "veto": ""}}})
        assert exc.value.status == "http_401"
        assert len(requests) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_decide_deadline_exhaustion_raises_timeout():
    def responder(call_idx: int, req: dict):
        time.sleep(0.1)
        return 200, _canonical_choice_response(), None

    server, port, _requests = start_stub(responder)
    try:
        # timeout_s very small
        ad = DecisionAdapter("systemone", f"http://127.0.0.1:{port}", "m", "K", None, 0.03, "k")
        with pytest.raises(DecisionError) as exc:
            ad.decide({}, {"q_choice": {"type": "choice", "criteria": {"proceed": "", "hold": "", "veto": ""}}})
        assert exc.value.status == "timeout"
    finally:
        server.shutdown()
        server.server_close()


def test_decide_network_error_with_healthy_deadline_raises_network(monkeypatch):
    # Simulate a socket/network TimeoutError occurring when the overall deadline is still healthy
    def _fake_urlopen(req, timeout):
        raise urllib.error.URLError(TimeoutError("socket timed out"))

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    ad = DecisionAdapter("systemone", "http://127.0.0.1:9", "m", "K", None, 30.0, "k")
    with pytest.raises(DecisionError) as exc:
        ad.decide({}, {"q_choice": {"type": "choice", "criteria": {"proceed": "", "hold": "", "veto": ""}}})
    assert exc.value.status == "network"


def test_decide_connection_refused_raises_network():
    # Connecting to closed port on loopback
    ad = DecisionAdapter("systemone", "http://127.0.0.1:1", "m", "K", None, 5.0, "k")
    with pytest.raises(DecisionError) as exc:
        ad.decide({}, {"q_choice": {"type": "choice", "criteria": {"proceed": "", "hold": "", "veto": ""}}})
    assert exc.value.status == "network"


# ── Malformed response variants ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "bad_payload",
    [
        b"not json at all",
        b"\xff\xfe\x00\x00invalid utf8",
        json.dumps([]).encode("utf-8"),
        json.dumps("string").encode("utf-8"),
        json.dumps({"model": "m"}).encode("utf-8"),  # answers missing
        json.dumps({"answers": []}).encode("utf-8"),  # answers not dict
        json.dumps({"answers": {}}).encode("utf-8"),  # question missing
        json.dumps({"answers": {"q_choice": "not-a-dict"}}).encode("utf-8"),
        json.dumps({"answers": {"q_choice": {"type": "noul", "noul": 0.5}}}).encode("utf-8"),  # type mismatch
        # choice: value not in criteria
        json.dumps({
            "answers": {
                "q_choice": {
                    "type": "choice",
                    "choice": "bogus_option",
                    "probabilities": {"proceed": 0.8, "hold": 0.1, "veto": 0.1},
                    "confidence": 0.8,
                }
            }
        }).encode("utf-8"),
        # choice: probabilities missing key
        json.dumps({
            "answers": {
                "q_choice": {
                    "type": "choice",
                    "choice": "proceed",
                    "probabilities": {"proceed": 0.9, "hold": 0.1},
                    "confidence": 0.8,
                }
            }
        }).encode("utf-8"),
        # choice: probability value not finite / bool / out of range
        json.dumps({
            "answers": {
                "q_choice": {
                    "type": "choice",
                    "choice": "proceed",
                    "probabilities": {"proceed": 1.5, "hold": 0.1, "veto": 0.1},
                    "confidence": 0.8,
                }
            }
        }).encode("utf-8"),
        json.dumps({
            "answers": {
                "q_choice": {
                    "type": "choice",
                    "choice": "proceed",
                    "probabilities": {"proceed": True, "hold": 0.1, "veto": 0.1},
                    "confidence": 0.8,
                }
            }
        }).encode("utf-8"),
        # choice: confidence is bool
        json.dumps({
            "answers": {
                "q_choice": {
                    "type": "choice",
                    "choice": "proceed",
                    "probabilities": {"proceed": 0.8, "hold": 0.1, "veto": 0.1},
                    "confidence": True,
                }
            }
        }).encode("utf-8"),
        # giant integer in JSON -> malformed without OverflowError
        b'{"answers": {"q_choice": {"type": "choice", "choice": "proceed", "probabilities": {"proceed": 1' + b'0' * 500 + b', "hold": 0, "veto": 0}, "confidence": 0.8}}}',
    ],
)
def test_decide_malformed_variants(bad_payload):
    server, port, _ = start_stub(lambda i, r: (200, None, bad_payload))
    try:
        ad = DecisionAdapter("systemone", f"http://127.0.0.1:{port}", "m", "K", None, 10.0, "k")
        with pytest.raises(DecisionError) as exc:
            ad.decide({}, {"q_choice": {"type": "choice", "criteria": {"proceed": "", "hold": "", "veto": ""}}})
        assert exc.value.status == "malformed"
    finally:
        server.shutdown()
        server.server_close()


# ── Fuzz test (>= 2000 deterministic mutations) ──────────────────────────────

_FUZZ_JUNK = [
    None,
    True,
    False,
    0,
    1,
    -1,
    1.5,
    float("nan"),
    float("inf"),
    float("-inf"),
    10**400,
    "",
    "bogus",
    [],
    [1, 2],
    {},
    {"nested": "dict"},
]


def _all_paths(data: Any, prefix: tuple = ()) -> list[tuple]:
    paths: list[tuple] = []
    if isinstance(data, dict):
        for k, v in data.items():
            p = prefix + (k,)
            paths.append(p)
            paths.extend(_all_paths(v, p))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            p = prefix + (i,)
            paths.append(p)
            paths.extend(_all_paths(v, p))
    return paths


def test_fuzz_decide_contract_never_raises_unexpected_exceptions():
    """Fuzz >= 2000 mutated server responses: only DecisionError is raised."""
    rng = random.Random(20260927)
    base = _canonical_choice_response()
    questions = {"q_choice": {"type": "choice", "criteria": {"proceed": "", "hold": "", "veto": ""}}}

    class MockResponse:
        def __init__(self, data: bytes):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return self._data

    ad_mock = DecisionAdapter("systemone", "http://127.0.0.1:0", "m", "K", None, 10.0, "k")
    violations: list[str] = []

    for i in range(2000):
        data = copy.deepcopy(base)
        op = i % 3
        paths = _all_paths(data)

        if op == 0 and paths:  # Replace node with junk
            p = rng.choice(paths)
            curr = data
            for seg in p[:-1]:
                curr = curr[seg]
            curr[p[-1]] = rng.choice(_FUZZ_JUNK)
        elif op == 1 and paths:  # Delete node
            dict_paths = [p for p in paths if isinstance(p[-1], str)]
            if dict_paths:
                p = rng.choice(dict_paths)
                curr = data
                for seg in p[:-1]:
                    curr = curr[seg]
                del curr[p[-1]]
        elif op == 2 and paths:  # Nest node deeper
            p = rng.choice(paths)
            curr = data
            for seg in p[:-1]:
                curr = curr[seg]
            curr[p[-1]] = {"extra_nested": curr[p[-1]]}

        raw_bytes = json.dumps(data).encode("utf-8")

        def _mock_urlopen(*args, _payload=raw_bytes, **kwargs):
            return MockResponse(_payload)

        orig_urlopen = urllib.request.urlopen
        urllib.request.urlopen = _mock_urlopen
        try:
            res = ad_mock.decide("state", questions)
            # If it succeeded, check fields
            assert isinstance(res, Decision)
        except DecisionError as de:
            assert de.status in ("malformed", "timeout", "network") or de.status.startswith("http_")
        except Exception as exc:
            violations.append(f"Iteration {i} raised unexpected {type(exc).__name__}: {exc}")
        finally:
            urllib.request.urlopen = orig_urlopen

    assert not violations, f"Fuzz violations: {violations[:5]}"


# ── Hub validate & redact tests ──────────────────────────────────────────────

def test_hub_validate_rejects_inline_api_key_in_decision_adapter():
    cfg = {
        "model": "m",
        "adapter": {"type": "openai"},
        "decision_adapter": {
            "type": "experiential",
            "api_key": "raw-key-secret",
        },
    }
    errs = hub_config.validate(cfg)
    assert any("decision_adapter.api_key is not allowed" in e for e in errs)


def test_hub_validate_rejects_unknown_keys_in_decision_adapter():
    cfg = {
        "model": "m",
        "adapter": {"type": "openai"},
        "decision_adapter": {
            "type": "experiential",
            "mystery_key": "foo",
        },
    }
    errs = hub_config.validate(cfg)
    assert any("decision_adapter unknown key 'mystery_key'" in e for e in errs)


def test_hub_validate_rejects_invalid_type_in_decision_adapter():
    cfg = {
        "model": "m",
        "adapter": {"type": "openai"},
        "decision_adapter": {
            "type": "not-a-type",
        },
    }
    errs = hub_config.validate(cfg)
    assert any("decision_adapter.type must be one of" in e for e in errs)


def test_hub_validate_accepts_valid_decision_adapter():
    cfg = {
        "model": "m",
        "adapter": {"type": "openai"},
        "decision_adapter": {
            "type": "experiential",
        },
    }
    assert hub_config.validate(cfg) == []


def test_hub_redact_masks_api_key_in_decision_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSCIO_VAULT_DIR", str(tmp_path))
    (tmp_path / "EXP_KEY").write_text("secret-in-vault\n")

    cfg = {
        "model": "m",
        "adapter": {"type": "openai"},
        "decision_adapter": {
            "type": "experiential",
            "api_key": "raw-inline-secret",
            "api_key_env": "EXP_KEY",
        },
    }
    redacted = hub_config.redact(cfg)
    da = redacted["decision_adapter"]
    assert "api_key" not in da
    assert da["api_key_present"] is True
    assert da["type"] == "experiential"


# ── G33b Construction validation and security teeth ─────────────────────────

def test_direct_construction_with_file_url_raises_valueerror_and_no_io(monkeypatch):
    def _explode(*args, **kwargs):
        raise AssertionError("urlopen must NEVER be called on invalid base_url construction")

    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    with pytest.raises(ValueError, match="Invalid base_url"):
        DecisionAdapter("systemone", "file:///etc/hostname#", "m", "K", None, 10.0, "secret-key")


@pytest.mark.parametrize("bad_timeout", [float("nan"), float("inf"), 0, -1, -0.5, 0.0, True, False])
def test_construction_invalid_timeout_s_raises_valueerror(bad_timeout):
    with pytest.raises(ValueError, match="timeout_s must be"):
        DecisionAdapter("systemone", "https://api.example.com", "m", "K", None, bad_timeout, "secret-key")


@pytest.mark.parametrize("bad_key", ["secret\nkey", "secret\x00key", "secret\rkey", ""])
def test_construction_invalid_api_key_raises_valueerror(bad_key):
    with pytest.raises(ValueError, match="api_key"):
        DecisionAdapter("systemone", "https://api.example.com", "m", "K", None, 10.0, bad_key)


def test_transport_exception_never_leaks_secret_key_in_str_repr_args_traceback(monkeypatch):
    secret_key = "TOP_SECRET_API_KEY_xyz123"
    ad = DecisionAdapter("systemone", "https://api.example.com", "m", "K", None, 10.0, secret_key)

    def _exploding_urlopen(req, timeout):
        raise ValueError(f"bad header Authorization: Bearer {secret_key}")

    monkeypatch.setattr(urllib.request, "urlopen", _exploding_urlopen)

    with pytest.raises(DecisionError) as exc_info:
        ad.decide({"state": "ok"}, {"q1": {"type": "noul"}})

    err = exc_info.value
    assert err.status == "network"
    assert secret_key not in str(err)
    assert secret_key not in repr(err)
    for arg in err.args:
        assert secret_key not in str(arg)

    tb_str = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    assert secret_key not in tb_str


def test_local_validation_state_rejects_nan_and_non_serializable_without_io(monkeypatch):
    ad = DecisionAdapter("systemone", "https://api.example.com", "m", "K", None, 10.0, "key")

    def _explode(*args, **kwargs):
        raise AssertionError("urlopen must NEVER be called on invalid state")

    monkeypatch.setattr(urllib.request, "urlopen", _explode)

    # State with NaN
    with pytest.raises(ValueError, match="state is not valid JSON"):
        ad.decide({"metric": float("nan")}, {"q1": {"type": "noul"}})

    # State with Inf
    with pytest.raises(ValueError, match="state is not valid JSON"):
        ad.decide({"metric": float("inf")}, {"q1": {"type": "noul"}})

    # State with non-serializable object
    class Unserializable:
        pass

    with pytest.raises(ValueError, match="state is not valid JSON"):
        ad.decide({"obj": Unserializable()}, {"q1": {"type": "noul"}})
