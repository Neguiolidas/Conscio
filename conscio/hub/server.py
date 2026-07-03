"""Conscio Hub localhost HTTP server (stdlib only).

`route()` is a pure dispatch function (unit-tested without a socket). `Handler`
is the thin BaseHTTPRequestHandler adapter. Binds 127.0.0.1 by default; never
0.0.0.0. Serves a fixed static whitelist — no path traversal.

v2.7.1: API-key vault (raw keys -> ~/.config/conscio/keys, 0600) + provider
resolve merge.
"""
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
from . import config, control, model_test, providers

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


# ── Route dispatch ───────────────────────────────────────────────────

def route(method: str, path: str, query: dict, body: dict | None,
          *, token: str | None, auth: str | None,
          storage: Path | None = None, daemon_control: bool = False) -> Resp:
    if path.startswith("/api/") and token:
        expected = f"Bearer {token}"
        if not (auth and hmac.compare_digest(auth, expected)):
            return _err(401, "unauthorized")

    if path == "/api/health" and method == "GET":
        return Resp(200, {"ok": True, "version": __version__,
                          "config_path": str(config.config_path()),
                          "daemon_control": bool(daemon_control),
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
            adapter = dict(body["adapter"])
        else:
            try:
                pc = providers.resolve_provider(cfg, body.get("provider", ""))
            except KeyError:
                return _err(404, "unknown provider", body.get("provider"))
            adapter = {"type": pc["type"]}
            if pc.get("base_url"):
                adapter["base_url"] = pc["base_url"]
            if pc.get("api_key_env"):
                adapter["api_key_env"] = pc["api_key_env"]
        # Top-level overrides from UI fields always win over resolved defaults
        if body.get("base_url"):
            adapter["base_url"] = body["base_url"]
        # Handle raw API key: store in vault, replace with api_key_env reference
        raw_key = body.get("api_key") or adapter.pop("api_key", None)
        if raw_key:
            env_name = config._env_name_for(adapter.get("type", ""), body.get("model", ""))
            config.vault_store(env_name, raw_key)
            adapter["api_key_env"] = env_name
        # Preserve existing api_key_env if no new key provided
        elif "api_key_env" not in adapter:
            existing = (cfg.get("adapter") or {}).get("api_key_env")
            if existing:
                adapter["api_key_env"] = existing
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

    if path == "/api/daemon/awake" and method == "PUT":
        if not daemon_control:
            return _err(404, "not found", path)
        if not isinstance(body, dict) or not isinstance(body.get("awake"), bool):
            return _err(400, "awake (bool) required")
        if storage is None:       # never fall back to the config dir — no daemon
            return _err(500, "daemon control enabled but no storage configured")
        sdir = Path(storage)
        if not sdir.is_dir():                       # reserva: no silent write
            return _err(500, "storage dir does not exist", str(sdir))
        return Resp(200, control.write_control(sdir, body["awake"]))

    if path == "/api/daemon/control" and method == "GET":
        if not daemon_control:
            return _err(404, "not found", path)
        if storage is None:
            return _err(500, "daemon control enabled but no storage configured")
        return Resp(200, control.read_control(Path(storage)))

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


def _bind_vault_dir(vault_dir: "str | None") -> None:
    """Point every vault_* call in this process at a per-host vault (R2).
    The installer binds each host's MCP/daemon to a space vault via
    CONSCIO_VAULT_DIR; a Hub managing that space's keys must be launched with
    the SAME dir, or its keys land in the global vault the space never reads."""
    if vault_dir:
        os.environ["CONSCIO_VAULT_DIR"] = str(Path(vault_dir))


def _check_host(host: str) -> None:
    """Refuse any non-loopback bind — the Hub is localhost-only by contract.
    Makes the module docstring's 'never 0.0.0.0' literally true."""
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise ValueError(
        f"refusing non-loopback host {host!r}: Conscio Hub binds loopback only")


def make_server(host: str, port: int, token: str | None, *,
                storage: Path | None = None,
                daemon_control: bool = False) -> ThreadingHTTPServer:
    _check_host(host)
    if daemon_control and storage is None:
        # fail at construction, not per-request — without a storage dir the
        # awake toggle would serve nothing but 500s (there is deliberately no
        # config-dir fallback: the daemon only reads control from storage)
        raise ValueError("daemon control requires a storage dir "
                         "(pass --storage matching the daemon's)")
    class _H(Handler):
        _token = token
        _storage = storage
        _daemon_control = daemon_control
    return ThreadingHTTPServer((host, port), _H)


class Handler(BaseHTTPRequestHandler):
    _token: str | None = None
    _storage: Path | None = None
    _daemon_control: bool = False

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
                         auth=self.headers.get("Authorization"),
                         storage=self._storage,
                         daemon_control=self._daemon_control)
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
    p.add_argument("--storage", default=None,
                   help="instance storage dir (default ~/.hermes/consciousness); "
                        "where the daemon control file is written — must match "
                        "the daemon's --storage or the awake toggle is a no-op")
    p.add_argument("--vault-dir", default=None,
                   help="per-host key vault dir (a space's <storage>/keys); "
                        "default: $CONSCIO_VAULT_DIR or the global vault")
    p.add_argument("--enable-daemon-control", action="store_true",
                   help="expose the awake toggle (writes daemon_control.json; "
                        "the daemon must run with --watch-control to honor it)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _arg_parser().parse_args(argv)
    _bind_vault_dir(args.vault_dir)
    # Default matches ConsciousnessEngine.DEFAULT_STORAGE (engine.py:133); kept as
    # a literal so the Hub stays engine-free (no engine import at launch).
    storage = (Path(args.storage) if args.storage
               else Path.home() / ".hermes" / "consciousness")
    try:
        srv = make_server(args.host, args.port, args.token,
                          storage=storage,
                          daemon_control=args.enable_daemon_control)
    except ValueError as exc:
        print(f"conscio-hub: {exc}")
        return 2
    print(f"conscio-hub on http://{args.host}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
