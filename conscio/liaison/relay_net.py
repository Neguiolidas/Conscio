# conscio/liaison/relay_net.py
"""Cross-machine relay transport (v4.5) — bridge peers over HTTP.

The relay normally shares a single liaison.db (same filesystem). When the
agent runs on ANOTHER machine (local network / tailscale), this module
provides the bridge between the shared-MB model and a remote peer:

  make_server(...)         → HTTP listener on a host/port (tailscale IP or
                             LAN). Receives `POST /relay/msg` from peer
                             machines and writes the message into the LOCAL
                             liaison.db via mailbox.send (same row shape as a
                             same-filesystem relay send).
  transport_send(url, msg) → POSTs a message dict to a peer's endpoint.
                             Tailscale just makes the peer reachable at a
                             100.x / MagicDNS address — the transport is
                             plain HTTP (token-protected).

Auth: a shared token is required on POST via
`Authorization: Bearer <token>`. The tailnet is already a private network;
the token keeps /api-layer safety minimal and stops accidental LAN writes.

Agnostic & universal: the message shape is the same `{from,to,type,payload}`
used everywhere in liaison, so any agent environment can both produce and
consume it. Engine-free, never raises — transport failures return False /
401 without crashing the caller loop.
"""

from __future__ import annotations

import hmac
import json
import logging
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerror
from urllib import request

from . import mailbox, relay

log = logging.getLogger("conscio.liaison.relay_net")
MAX_BODY_BYTES = 64 * 1024                    # hard wall, above relay cap
_AUTH_HEADER = "Authorization"


# ── validation ─────────────────────────────────────────────────────────

def validate_msg(msg: dict) -> None:
    """Raise ValueError on any violation (mirrors relay.validate_send).

    Deliberately does NOT enforce the local `--relay-peer` allowlist on `to`:
    remote peers are machines, not the local address allowlist. The network
    transport trusts the sender (auth by shared token), exactly as a
    shared-filesystem relay write would.
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


# ── inbound: write a remote message into the local mailbox ─────────────

def handle_inbound(db: Path, self_id: str, msg: dict) -> bool:
    """Persist a remote relay message into the local liaison.db.

    The receiver (this node) is the mailbox owner, so a message whose `to`
    isn't this node is still filed to `self_id` (mailbox is per-instance,
    not per-address). Returns True on success (or False on any malformed/
    oversized input — never raises).
    """
    try:
        validate_msg(msg)
    except ValueError:
        log.warning("relay_net: dropped malformed inbound")
        return False
    if relay.payload_size(msg["payload"]) > relay.MAX_PAYLOAD_BYTES:
        log.warning("relay_net: inbound payload over relay cap — dropped")
        return False
    try:
        # identity do remetente (envelope) — vem do corpo, o runtime local
        # não conhece o remetente de outra máquina; usamos a identidade MAC
        # declarada (a mesma que relayer gravaria num share-fs).
        identity = {"id": msg["from"], "modelo": "", "familia": "",
                    "runtime": "relay-net", "papel": "peer"}
        meta = msg.get("payload", {}).get("_meta", {}).get("from")
        if isinstance(meta, dict) and meta.get("id"):
            identity.update({k: meta.get(k, "") for k in
                             ("modelo", "familia", "runtime", "papel")})
            identity["id"] = meta["id"]
        mailbox.send(db, from_instance=identity["id"],
                     to_instance=self_id,
                     type=msg["type"], payload=msg["payload"],
                     identity=identity)
        return True
    except (sqlite3.Error, OSError, TypeError, ValueError):
        log.exception("relay_net: inbound write failed")
        return False


# ── HTTP server ────────────────────────────────────────────────────────

class RelayHandler(BaseHTTPRequestHandler):
    _db_path: Path | None = None
    _self_id: str = ""
    _token: str | None = None

    def log_message(self, format: str, *args):        # no token leak in logs
        pass

    def _unauthorized(self):
        self.send_response(401)
        self.end_headers()
        self.wfile.write(b"unauthorized")

    def do_POST(self):
        if self.path.rstrip("/") != "/relay/msg":
            self.send_response(404); self.end_headers(); return
        if self._db_path is None or not self._self_id:
            self.send_response(503); self.end_headers(); return
        expected = f"Bearer {self._token}"
        auth = self.headers.get(_AUTH_HEADER, "")
        if not (expected and hmac.compare_digest(auth, expected)):
            self._unauthorized(); return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("bad content length")
            raw = self.rfile.read(length)
            msg = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            self.send_response(400); self.end_headers()
            self.wfile.write(b"bad request")
            return
        ok = handle_inbound(self._db_path, self._self_id, msg)
        self.send_response(200 if ok else 400)
        self.end_headers()
        self.wfile.write(b"ok" if ok else b"rejected")

    def do_GET(self):
        """Liveness/activity probe for peers — `GET /relay/health`.

        Lets any peer on the tailnet detect that this relay node is alive
        BEFORE sending (no hook side effects, no mailbox write). Same Bearer
        auth as POST. Returns node identity + which agents are registered
        alive (drives the 'relay is active' indicator).
        """
        if self.path.rstrip("/") != "/relay/health":
            self.send_response(404); self.end_headers(); return
        expected = f"Bearer {self._token}"
        auth = self.headers.get(_AUTH_HEADER, "")
        if not (expected and hmac.compare_digest(auth, expected)):
            self._unauthorized(); return
        try:
            from . import agents
            alive = agents.list_agents(
                self._db_path, include_stale=False) if self._db_path else []
            body = json.dumps({
                "ok": True,
                "self_id": self._self_id,
                "db": str(self._db_path) if self._db_path else None,
                "agents_alive": [a.get("instance_id") for a in alive],
                "ts": time.time(),
            }, ensure_ascii=False).encode("utf-8")
        except (sqlite3.Error, OSError, ValueError):
            self.send_response(500); self.end_headers()
            self.wfile.write(b"error")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(host: str, port: int, token: str, db: Path,
                self_id: str) -> ThreadingHTTPServer:
    RelayHandler._db_path = Path(db)
    RelayHandler._self_id = self_id
    RelayHandler._token = token or ""
    return ThreadingHTTPServer((host, port), RelayHandler)


# ── client ─────────────────────────────────────────────────────────────

def transport_send(base_url: str, msg: dict, *, token: str,
                   timeout: float = 5.0) -> bool:
    """POST a relay message dict to a peer's endpoint. True on 2xx."""
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