# conscio/mcp/protocol.py
"""MCP method dispatch over the JSON-RPC transport. Pure routing + shaping;
no engine logic (that lives behind `bindings`)."""
from __future__ import annotations

from typing import Any

from .jsonrpc import (INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST,
                      METHOD_NOT_FOUND, InvalidParams, MethodNotFound,
                      make_error, make_response)

SUPPORTED_PROTOCOLS = ["2024-11-05", "2025-03-26", "2025-06-18"]


class Dispatcher:
    def __init__(self, bindings: Any) -> None:
        self.b = bindings
        self.initialized = False

    def handle(self, msg: dict) -> dict | None:
        if msg.get("jsonrpc") != "2.0":
            return make_error(msg.get("id"), INVALID_REQUEST,
                              "jsonrpc must be '2.0'")
        method = msg.get("method")
        if not isinstance(method, str):     # malformed/missing → falls through to MethodNotFound
            method = ""
        mid = msg.get("id")
        is_notification = "id" not in msg
        params = msg.get("params") or {}

        if method == "notifications/initialized":
            self.initialized = True
            return None
        if method == "initialize":
            # Lenient lifecycle: a successful initialize enables subsequent
            # requests even if the client skips the initialized notification
            # (which, when sent, is handled above and is idempotent).
            self.initialized = True
            return make_response(mid, self._initialize(params))
        if method == "ping":
            return None if is_notification else make_response(mid, {})

        if not self.initialized:
            return None if is_notification else make_error(
                mid, INVALID_REQUEST, "server not initialized")

        try:
            result = self._route(method, params)
        except MethodNotFound as exc:
            return None if is_notification else make_error(
                mid, METHOD_NOT_FOUND, str(exc))
        except InvalidParams as exc:
            return None if is_notification else make_error(
                mid, INVALID_PARAMS, str(exc))
        except Exception:                       # never leak a traceback
            return None if is_notification else make_error(
                mid, INTERNAL_ERROR, "internal error")
        return None if is_notification else make_response(mid, result)

    def _route(self, method: str, params: dict) -> dict:
        if method == "tools/list":
            return {"tools": self.b.tool_defs()}
        if method == "tools/call":
            name = params.get("name")
            if not name:
                raise InvalidParams("missing tool name")
            return self.b.call_tool(name, params.get("arguments") or {})
        if method == "resources/list":
            return {"resources": self.b.resource_defs()}
        if method == "resources/read":
            uri = params.get("uri")
            if not uri:
                raise InvalidParams("missing resource uri")
            return self.b.read_resource(uri)
        raise MethodNotFound(f"unknown method '{method}'")

    def _initialize(self, params: dict) -> dict:
        hook = getattr(self.b, "on_initialize", None)
        if hook is not None:                 # v2.0.1: host manifest wiring
            hook(params)
        requested = params.get("protocolVersion")
        version = (requested if requested in SUPPORTED_PROTOCOLS
                   else SUPPORTED_PROTOCOLS[-1])
        return {"protocolVersion": version,
                "serverInfo": {"name": "conscio", "version": self.b.version()},
                "capabilities": {"tools": {}, "resources": {}},
                "conscio": self.b.conscio_meta()}
