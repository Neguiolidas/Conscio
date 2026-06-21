"""Conscio Hub localhost HTTP server (stdlib only).

`route()` is a pure dispatch function (unit-tested without a socket). `Handler`
is the thin BaseHTTPRequestHandler adapter. Binds 127.0.0.1 by default; never
0.0.0.0. Serves a fixed static whitelist — no path traversal."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import __version__
from . import config, model_test, providers

MAX_BODY_BYTES = 65536
_STATIC = Path(__file__).parent / "static"
_STATIC_WHITELIST = {"index.html", "app.js", "style.css"}
_CONTENT_TYPES = {".html": "text/html", ".js": "application/javascript",
                  ".css": "text/css"}


def parse_json_body(raw: bytes, max_bytes: int = MAX_BODY_BYTES) -> dict:
    if len(raw) > max_bytes:
        raise ValueError("body too large")
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("body must be a JSON object")
    return obj


@dataclass
class Resp:
    status: int
    payload: Any = None
    body: bytes | None = None
    content_type: str = "application/json"
    headers: dict = field(default_factory=dict)


def _err(status: int, error: str, detail: Any = None) -> Resp:
    return Resp(status, {"error": error, "detail": detail})


def route(method: str, path: str, query: dict, body: dict | None,
          *, token: str | None, auth: str | None) -> Resp:
    if path.startswith("/api/") and token:
        if auth != f"Bearer {token}":
            return _err(401, "unauthorized")

    if path == "/api/health" and method == "GET":
        return Resp(200, {"ok": True, "version": __version__,
                          "config_path": str(config.config_path()),
                          "token_required": bool(token)})

    if path == "/api/config" and method == "GET":
        return Resp(200, config.redact(config.load()))

    if path == "/api/providers" and method == "GET":
        return Resp(200, providers.catalog(config.load()))

    if path == "/api/config" and method == "PUT":
        if not isinstance(body, dict) or not body.get("model"):
            return _err(400, "model required")
        cfg = config.load()
        if isinstance(body.get("adapter"), dict):
            adapter = dict(body["adapter"])            # explicit adapter (API form)
        else:                                          # resolve provider (name|type)
            try:
                pc = providers.resolve_provider(cfg, body.get("provider", ""))
            except KeyError:
                return _err(404, "unknown provider", body.get("provider"))
            adapter = {"type": pc["type"]}
            if pc.get("base_url"):
                adapter["base_url"] = pc["base_url"]
            if pc.get("api_key_env"):
                adapter["api_key_env"] = pc["api_key_env"]
        adapter["model"] = body["model"]
        cfg["model"] = body["model"]
        cfg["adapter"] = adapter
        errs = config.validate(cfg)
        if errs:
            return _err(400, "invalid config", errs)
        config.save(cfg)
        return Resp(200, config.redact(cfg))

    if path == "/api/providers" and method == "POST":
        if not isinstance(body, dict) or not body.get("name"):
            return _err(400, "provider needs a name")
        name = body["name"]
        entry = {k: v for k, v in body.items() if k != "name"}
        cfg = config.load()
        cfg.setdefault("providers", {})[name] = entry
        errs = config.validate(cfg)
        if errs:
            return _err(400, "invalid provider", errs)
        config.save(cfg)
        return Resp(200, providers.catalog(cfg))

    if path == "/api/models" and method == "GET":
        name = query.get("provider")
        if not name:
            return _err(400, "provider param required")
        try:
            pc = providers.resolve_provider(config.load(), name)
        except KeyError:
            return _err(404, "unknown provider", name)
        refresh = query.get("refresh") in ("1", "true", "yes")
        return Resp(200, providers.probe_models(pc, refresh=refresh))

    if path == "/api/model/test" and method == "POST":
        if not isinstance(body, dict) or not body.get("model"):
            return _err(400, "model required")
        try:
            pc = providers.resolve_provider(config.load(),
                                            body.get("provider", ""))
        except KeyError:
            return _err(404, "unknown provider", body.get("provider"))
        return Resp(200, model_test.smoke_test(pc, body["model"]))

    if method == "GET" and (path == "/" or path.startswith("/static/")):
        name = "index.html" if path == "/" else path.rsplit("/", 1)[-1]
        if name not in _STATIC_WHITELIST:
            return _err(404, "not found", path)
        fp = _STATIC / name
        try:
            data = fp.read_bytes()
        except OSError:
            return _err(404, "not found", name)
        ct = _CONTENT_TYPES.get(fp.suffix, "application/octet-stream")
        return Resp(200, body=data, content_type=ct)

    return _err(404, "not found", path)


def make_server(host: str, port: int, token: str | None) -> ThreadingHTTPServer:
    class _H(Handler):
        _token = token
    return ThreadingHTTPServer((host, port), _H)


class Handler(BaseHTTPRequestHandler):
    _token: str | None = None

    def log_message(self, *a):                     # never log (urls may carry keys)
        pass

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        body = None
        if method in ("POST", "PUT"):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                return self._send(_err(413, "body too large"))
            try:
                body = parse_json_body(self.rfile.read(length))
            except ValueError as exc:
                return self._send(_err(400, "bad body", str(exc)))
        try:
            resp = route(method, parsed.path, query, body,
                         token=self._token,
                         auth=self.headers.get("Authorization"))
        except Exception as exc:                    # no traceback leak
            resp = _err(500, "internal error", type(exc).__name__)
        self._send(resp)

    def _send(self, resp: Resp) -> None:
        if resp.body is not None:
            payload = resp.body
        else:
            payload = json.dumps(resp.payload).encode("utf-8")
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type)
        self.send_header("Content-Length", str(len(payload)))
        for k, v in resp.headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")


def _arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="conscio-hub",
                                description="Conscio Hub localhost control plane")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--token", default=os.environ.get("CONSCIO_HUB_TOKEN") or None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _arg_parser().parse_args(argv)
    srv = make_server(args.host, args.port, args.token)
    print(f"conscio-hub on http://{args.host}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
