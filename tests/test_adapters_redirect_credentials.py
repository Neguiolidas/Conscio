"""Tests verifying that HTTP redirect does not leak credentials in adapters (Slice 1)."""
from __future__ import annotations

import ast
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from conscio.agency import adapters
from conscio.agency.adapter import AdapterBadResponse, AdapterHTTPError
from conscio.agency.adapters import _post_json


# ---------------------------------------------------------------------------
# T1: Parametrize 301/302/303 — AdapterHTTPError raised and B receives zero requests
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("redirect_code", [301, 302, 303])
def test_adapters_redirect_refused_no_requests_to_target(redirect_code: int) -> None:
    received_headers_b: list[dict[str, str]] = []

    class _HandlerB(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received_headers_b.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok_b"}')

        def do_POST(self) -> None:
            received_headers_b.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok_b"}')

        def log_message(self, *args: Any) -> None:
            pass

    server_b = HTTPServer(("127.0.0.1", 0), _HandlerB)
    thread_b = threading.Thread(target=server_b.serve_forever, daemon=True)
    thread_b.start()

    try:
        port_b = server_b.server_port

        class _HandlerA(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(redirect_code)
                self.send_header("Location", f"http://127.0.0.1:{port_b}/redirected")
                self.end_headers()

            def log_message(self, *args: Any) -> None:
                pass

        server_a = HTTPServer(("127.0.0.1", 0), _HandlerA)
        thread_a = threading.Thread(target=server_a.serve_forever, daemon=True)
        thread_a.start()

        try:
            port_a = server_a.server_port
            headers = {
                "Authorization": "Bearer secret-token-xyz",
                "x-api-key": "secret-key-123",
            }
            with pytest.raises(AdapterHTTPError) as exc_info:
                _post_json(
                    f"http://127.0.0.1:{port_a}/entry",
                    {"prompt": "hello"},
                    timeout=5.0,
                    headers=headers,
                )

            assert exc_info.value.status == redirect_code
            assert len(received_headers_b) == 0, (
                f"Server B received requests: {received_headers_b}"
            )
        finally:
            server_a.shutdown()
            server_a.server_close()
    finally:
        server_b.shutdown()
        server_b.server_close()


# ---------------------------------------------------------------------------
# T2: Legitimate case: direct POST with credentials
# ---------------------------------------------------------------------------
def test_adapters_direct_post_with_credentials_succeeds() -> None:
    received_headers: list[dict[str, str]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received_headers.append(dict(self.headers))
            body = json.dumps({"status": "ok", "echo": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_port
        headers = {
            "Authorization": "Bearer legitimate-token-456",
            "x-api-key": "legitimate-key-789",
        }
        res = _post_json(
            f"http://127.0.0.1:{port}/direct",
            {"message": "ping"},
            timeout=5.0,
            headers=headers,
        )
        assert res == {"status": "ok", "echo": True}
        assert len(received_headers) == 1
        lowered = {k.lower(): v for k, v in received_headers[0].items()}
        assert lowered.get("authorization") == "Bearer legitimate-token-456"
        assert lowered.get("x-api-key") == "legitimate-key-789"
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# T3: Error message names destination origin only (no path, fragment, userinfo, or query leak)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("location_header", "forbidden_token", "expected_origin"),
    [
        (
            "https://api.example.com/v1/keys/sk-ABC123DEF456GHJ789/callback",
            "sk-ABC123DEF456GHJ789",
            "https://api.example.com",
        ),
        (
            "https://api.example.com/cb#access_token=eyJhbGciOi.secret",
            "access_token",
            "https://api.example.com",
        ),
        (
            "https://user:pw@api.example.com/x",
            "pw",
            "https://api.example.com",
        ),
        (
            "http://127.0.0.1:59999/target/path?key=SUPER_CONFIDENTIAL_SECRET",
            "SUPER_CONFIDENTIAL_SECRET",
            "http://127.0.0.1:59999",
        ),
        (
            "data:text/plain,SECRET",
            "SECRET",
            "?",
        ),
        (
            "mailto:SECRET@x.com",
            "SECRET",
            "?",
        ),
    ],
)
def test_adapters_redirect_error_message_drops_secrets(
    location_header: str, forbidden_token: str, expected_origin: str
) -> None:
    class _HandlerA(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(302)
            self.send_header("Location", location_header)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server_a = HTTPServer(("127.0.0.1", 0), _HandlerA)
    thread_a = threading.Thread(target=server_a.serve_forever, daemon=True)
    thread_a.start()

    try:
        port_a = server_a.server_port
        with pytest.raises(AdapterHTTPError) as exc_info:
            _post_json(
                f"http://127.0.0.1:{port_a}/entry",
                {"prompt": "hello"},
                timeout=5.0,
            )

        msg = str(exc_info.value)
        assert forbidden_token not in msg
        assert expected_origin in msg
        assert (
            f"redirect to {expected_origin} refused; point base_url at the final address"
            in msg
        )
    finally:
        server_a.shutdown()
        server_a.server_close()


def test_origin_schemes_without_host_return_question_mark() -> None:
    assert adapters._origin("data:text/plain,SECRET") == "?"
    assert adapters._origin("mailto:SECRET@x.com") == "?"



def test_adapters_redirect_error_message_relative_location() -> None:
    class _HandlerA(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(302)
            self.send_header("Location", "/v2/x")
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server_a = HTTPServer(("127.0.0.1", 0), _HandlerA)
    thread_a = threading.Thread(target=server_a.serve_forever, daemon=True)
    thread_a.start()

    try:
        port_a = server_a.server_port
        with pytest.raises(AdapterHTTPError) as exc_info:
            _post_json(
                f"http://127.0.0.1:{port_a}/entry",
                {"prompt": "hello"},
                timeout=5.0,
            )

        msg = str(exc_info.value)
        expected_origin = f"http://127.0.0.1:{port_a}"
        assert expected_origin in msg
        assert "/v2/x" not in msg
        assert (
            f"redirect to {expected_origin} refused; point base_url at the final address"
            in msg
        )
    finally:
        server_a.shutdown()
        server_a.server_close()



# ---------------------------------------------------------------------------
# T4: Response ceiling (MAX_RESPONSE_BYTES)
# ---------------------------------------------------------------------------
def test_adapters_max_response_bytes_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    limit = 1024
    monkeypatch.setattr(adapters, "MAX_RESPONSE_BYTES", limit)

    response_body = b""

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_port
        url = f"http://127.0.0.1:{port}/test"

        # 1. Boundary: exactly 1024 bytes -> MUST pass
        # {"pad": "..."} padded to exactly 1024 bytes
        prefix = b'{"pad": "'
        suffix = b'"}'
        pad_len = limit - len(prefix) - len(suffix)
        body_exact = prefix + (b"x" * pad_len) + suffix
        assert len(body_exact) == limit
        response_body = body_exact
        res = _post_json(url, {}, timeout=5.0)
        assert len(res["pad"]) == pad_len

        # 2. Exceeding ceiling: 1025 bytes -> MUST raise AdapterBadResponse
        body_over = prefix + (b"x" * (pad_len + 1)) + suffix
        assert len(body_over) == limit + 1
        response_body = body_over
        with pytest.raises(AdapterBadResponse) as exc_info:
            _post_json(url, {}, timeout=5.0)
        assert f"response exceeds {limit} bytes" in str(exc_info.value)
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# T6: AST guard — conscio/http_safety.py imports nothing from conscio
# ---------------------------------------------------------------------------
def test_http_safety_has_no_conscio_imports() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    path = repo_root / "conscio" / "http_safety.py"
    assert path.exists(), f"{path} must exist"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("conscio"), (
                    f"Forbidden import from conscio found: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert not node.module.startswith("conscio"), (
                    f"Forbidden import from conscio found: {node.module}"
                )
            assert node.level == 0, (
                "Forbidden relative import found in http_safety.py"
            )
