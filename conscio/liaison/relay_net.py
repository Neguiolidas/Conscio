# conscio/liaison/relay_net.py
"""Cross-machine relay bridge (v4.5.4) — one postman per machine.

The relay is a shared filesystem between agents on the same box. When a peer
lives on ANOTHER machine (LAN / tailscale), this module carries the message
across:

  make_server(host, port, token) -> HTTP listener. Receives
                                    `POST /relay/msg` from remote machines
                                    and deposits each message into the
                                    RECIPIENT's spool.
  transport_send(url, msg)       -> POSTs a message dict to a peer bridge.

Until v4.5.3 the bridge wrote every inbound message into the mailbox of the
agent that happened to own the bridge (finding A11): a second local agent
could never be reached from outside, and the bridge owner received mail
addressed to somebody else. The bridge now owns no database at all — it is a
postman. Delivery is `spool.deposit(to, msg)`, and the recipient ingests it
on its own next tick, with the same code path as a local delivery.

Auth: a shared token on every request via `Authorization: Bearer <token>`.
The tailnet is already private; the token stops accidental LAN writes.

Engine-free, never raises: transport failures return False, malformed input
returns an HTTP status, nothing crashes the caller loop.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerror
from urllib import request

from . import directory, relay, spool

log = logging.getLogger("conscio.liaison.relay_net")
MAX_BODY_BYTES = 64 * 1024                    # hard wall, above relay cap
DEFAULT_BRIDGE_PORT = 8789     # stable on purpose: the peer stored this URL
_AUTH_HEADER = "Authorization"


# ── validation ─────────────────────────────────────────────────────────

def validate_msg(msg: dict) -> None:
    """Raise ValueError on any violation (mirrors relay.validate_send).

    Deliberately does NOT enforce a peer allowlist: an allowlist is about
    which agents I read, and this is the machine boundary. Auth here is the
    shared token; the recipient still applies its own rules on ingest.
    """
    if not isinstance(msg, dict):
        raise ValueError("msg must be a dict")
    for field in ("from", "to", "type", "payload"):
        if field not in msg:
            raise ValueError(f"missing field: {field}")
    if not isinstance(msg.get("from"), str) or not msg["from"]:
        raise ValueError("from must be a non-empty string")
    if not isinstance(msg.get("type"), str) or not msg["type"]:
        raise ValueError("type must be a non-empty string")
    if msg["type"] in relay.RESERVED_TYPES:
        raise ValueError(f"type {msg['type']!r} reserved for review channel")
    if not isinstance(msg.get("payload"), dict):
        raise ValueError("payload must be an object")
    if relay.payload_size(msg["payload"]) > relay.MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload exceeds {relay.MAX_PAYLOAD_BYTES} bytes")


# ── inbound: deposit into the recipient's spool ────────────────────────

def handle_inbound(msg: dict) -> tuple[int, str]:
    """Deposit a remote message into the RECIPIENT's spool.

    Returns `(http_status, reason)`. The bridge serves every agent on this
    machine, so `to` decides the destination — not who started the bridge.
    """
    try:
        validate_msg(msg)
    except ValueError as exc:
        return 400, str(exc)
    to = str(msg.get("to", ""))
    if not directory.valid_id(to):     # `to` arrives from the NETWORK: without
        return 400, "invalid recipient"    # this it can escape the spool root
    if directory.get(to) is None:
        return 404, "unknown recipient on this machine"
    try:
        spool.deposit(to, msg)
    except (OSError, ValueError) as exc:
        log.warning("relay_net: spool deposit failed: %s", exc)
        return 500, f"spool unavailable: {exc}"
    return 200, "ok"


# ── HTTP server ────────────────────────────────────────────────────────

class RelayHandler(BaseHTTPRequestHandler):
    """Per-server state only. Two bridges in one process used to share class
    attributes, so starting the second one silently retargeted the first
    (finding A12) — the token now lives on the server object."""

    def log_message(self, format: str, *args):        # no token leak in logs
        pass

    @property
    def _token(self) -> str:
        return getattr(self.server, "relay_token", "") or ""

    def _authorized(self) -> bool:
        expected = f"Bearer {self._token}"
        auth = self.headers.get(_AUTH_HEADER, "")
        return bool(self._token and hmac.compare_digest(auth, expected))

    def _reply(self, status: int, body: bytes,
               content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.rstrip("/") != "/relay/msg":
            self._reply(404, b"not found"); return
        if not self._authorized():
            self._reply(401, b"unauthorized"); return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("bad content length")
            msg = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._reply(400, b"bad request"); return
        status, reason = handle_inbound(msg)
        self._reply(status, reason.encode("utf-8"))

    def do_GET(self):
        """Liveness probe — `GET /relay/health`.

        Lets a peer check the bridge before sending (no side effects). Also
        lists the agents this machine can deliver to, which is what makes
        `conscio relay pair` able to say who is on the other side.
        """
        if self.path.rstrip("/") != "/relay/health":
            self._reply(404, b"not found"); return
        if not self._authorized():
            self._reply(401, b"unauthorized"); return
        try:
            local = [c["instance_id"] for c in directory.peers(exclude="")]
        except Exception:                    # a broken directory is still a
            local = []                       # live bridge — say so
        body = json.dumps({"ok": True, "role": "bridge", "agents": local,
                           "ts": time.time()},
                          ensure_ascii=False).encode("utf-8")
        self._reply(200, body, "application/json")


class _RelayServer(ThreadingHTTPServer):
    """Declares the token the handler reads off ``self.server``.

    Hanging the attribute on a plain ``ThreadingHTTPServer`` typechecks as an
    unknown-attribute write; declaring it here is the same runtime behaviour
    with the contract written down.
    """

    relay_token: str = ""


def make_server(host: str, port: int, token: str) -> ThreadingHTTPServer:
    """A bridge with no database: it only knows how to hand mail over."""
    srv = _RelayServer((host, port), RelayHandler)
    srv.relay_token = token or ""            # per-instance, never class state
    return srv


# ── client ─────────────────────────────────────────────────────────────

def transport_send(base_url: str, msg: dict, *, token: str,
                   timeout: float = 5.0) -> bool:
    """POST a relay message dict to a peer's bridge. True on 2xx."""
    try:
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            base_url.rstrip("/") + "/relay/msg", data=data,
            headers={"Content-Type": "application/json",
                     _AUTH_HEADER: f"Bearer {token}"},
            method="POST")
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urlerror.URLError, OSError, ValueError, json.JSONDecodeError):
        log.warning("relay_net: transport_send failed to %s", base_url)
        return False


# ── CLI ────────────────────────────────────────────────────────────────

def read_or_create_token(path: Path) -> str:
    """The bridge token, generated on first run. 0600, never printed."""
    path = Path(path)
    try:
        tok = path.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Bug-hunt: token written non-atomically; a crash mid-write left a
    # truncated token in the shared relay state. tmp+rename via guards.
    from ..guards import atomic_write_text
    atomic_write_text(path, tok)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return tok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="conscio-relay-bridge",
        description="HTTP bridge: cross-machine delivery into the "
                    "recipient's spool")
    ap.add_argument("--bind", default="127.0.0.1",
                    help="address to listen on (default 127.0.0.1; use the "
                         "tailscale IP to accept remote peers)")
    ap.add_argument("--port", type=int, default=DEFAULT_BRIDGE_PORT,
                    help=f"port (default {DEFAULT_BRIDGE_PORT}; 0 = ephemeral,"
                         " tests only — peers store this URL)")
    ap.add_argument("--token-file", default="",
                    help="token path (default: <relay root>/bridge.token)")
    args = ap.parse_args(argv)

    token_path = (Path(args.token_file) if args.token_file
                  else directory.relay_root() / "bridge.token")
    srv = make_server(args.bind, args.port, read_or_create_token(token_path))
    print(f"conscio-relay-bridge on {args.bind}:{srv.server_address[1]} "
          f"(token: {token_path})", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
