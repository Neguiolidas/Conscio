# conscio/observatory/server.py
"""Conscio Observatory localhost HTTP server (stdlib only).

`route()` is a pure dispatch function (unit-tested without a socket). Read-only:
serves GET only — every mutation verb returns 405. Binds 127.0.0.1 by default;
never 0.0.0.0. Self-contained: does NOT import the Hub (kept decoupled), but
mirrors its proven loopback + constant-time-token idioms."""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import __version__
from .projection import Projection

_STATIC = Path(__file__).parent / "static"
_STATIC_WHITELIST = {"index.html", "app.js", "style.css"}
_CONTENT_TYPES = {".html": "text/html", ".js": "application/javascript",
                  ".css": "text/css"}
_DEFAULT_STORAGE = Path.home() / ".hermes" / "consciousness"


@dataclass
class Resp:
    status: int
    payload: Any = None
    body: bytes | None = None
    content_type: str = "application/json"
    headers: dict = field(default_factory=dict)


def _err(status: int, error: str, detail: Any = None) -> Resp:
    return Resp(status, {"error": error, "detail": detail})


def _int(query: dict, key: str, default: int) -> int:
    try:
        return int(query.get(key, default))
    except (TypeError, ValueError):
        return default


def route(method: str, path: str, query: dict, *, projection: Projection,
          token: str | None, auth: str | None) -> Resp:
    # read-only: no mutation verb is ever served
    if method not in ("GET", "HEAD"):
        return _err(405, "method not allowed", method)

    if path.startswith("/api/") and token:
        expected = f"Bearer {token}"
        if not (auth and hmac.compare_digest(auth, expected)):
            return _err(401, "unauthorized")

    if path == "/api/health":
        return Resp(200, {"ok": True, "version": __version__,
                          "storage": str(projection.storage),
                          "token_required": bool(token)})
    if path == "/api/events":
        return Resp(200, projection.events(
            type=query.get("type") or None, category=query.get("category") or None,
            since=query.get("since") or None, limit=_int(query, "limit", 50)))
    if path == "/api/actions":
        return Resp(200, projection.actions(
            status=query.get("status") or None, limit=_int(query, "limit", 50)))
    if path == "/api/skills":
        return Resp(200, projection.skills(limit=_int(query, "limit", 100)))
    if path == "/api/goals":
        return Resp(200, projection.goals())
    if path == "/api/state":
        return Resp(200, projection.state())

    if path == "/" or path.startswith("/static/"):
        name = "index.html" if path == "/" else path.rsplit("/", 1)[-1]
        if name not in _STATIC_WHITELIST:
            return _err(404, "not found", path)
        try:
            data = (_STATIC / name).read_bytes()
        except OSError:
            return _err(404, "not found", name)
        ct = _CONTENT_TYPES.get(Path(name).suffix, "application/octet-stream")
        return Resp(200, body=data, content_type=ct)

    return _err(404, "not found", path)


def _check_host(host: str) -> None:
    """Refuse any non-loopback bind — the Observatory binds loopback only.
    Reference: conscio/hub/server.py:_check_host — keep in sync on security changes."""
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise ValueError(
        f"refusing non-loopback host {host!r}: Observatory binds loopback only")


def make_server(host: str, port: int, token: str | None,
                storage: Path) -> ThreadingHTTPServer:
    _check_host(host)
    projection = Projection(storage)

    class _H(Handler):
        _token = token
        _projection = projection

    return ThreadingHTTPServer((host, port), _H)


class Handler(BaseHTTPRequestHandler):
    _token: str | None = None
    _projection: Projection | None = None

    def log_message(self, *a):                 # never log (urls may carry tokens)
        pass

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            resp = route(method, parsed.path, query, projection=self._projection,
                         token=self._token, auth=self.headers.get("Authorization"))
        except Exception as exc:               # no traceback leak
            resp = _err(500, "internal error", type(exc).__name__)
        self._send(resp)

    def _send(self, resp: Resp) -> None:
        payload = resp.body if resp.body is not None else \
            json.dumps(resp.payload).encode("utf-8")
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


def _arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="conscio-observatory",
        description="Conscio Observatory — read-only localhost state viewer")
    p.add_argument("--storage", default=str(_DEFAULT_STORAGE),
                   help="instance storage dir (default: ~/.hermes/consciousness)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8788)
    p.add_argument("--token",
                   default=os.environ.get("CONSCIO_OBSERVATORY_TOKEN") or None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _arg_parser().parse_args(argv)
    try:
        server = make_server(args.host, args.port, args.token, Path(args.storage))
    except ValueError as exc:
        print(f"conscio-observatory: {exc}")
        return 2
    print(f"conscio-observatory on http://{args.host}:{args.port} "
          f"(storage={args.storage}, read-only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
