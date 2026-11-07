# conscio/mcp/protocol.py
"""MCP method dispatch over the JSON-RPC transport. Pure routing + shaping;
no engine logic (that lives behind `bindings`)."""
from __future__ import annotations

from typing import Any

from .jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    InvalidParams,
    MethodNotFound,
    make_error,
    make_response,
)

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
            try:
                return make_response(mid, self._initialize(params))
            except Exception:
                # The whole server dying over one bad host manifest is the
                # failure the client reports as "process exited" mid-session;
                # a broken hook costs the request, not the process.
                return None if is_notification else make_error(
                    mid, INTERNAL_ERROR, "initialize failed")
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
        # handle() only normalises falsy params; a truthy non-dict (e.g. a
        # string) would make every params.get below an AttributeError.
        # Coerce once, here, so no handler can raise it.
        if not isinstance(params, dict):
            params = {}
        if method == "tools/list":
            return {"tools": self.b.tool_defs()}
        if method == "tools/call":
            name = params.get("name")
            if not name:
                raise InvalidParams("missing tool name")
            return self.b.call_tool(name, params.get("arguments") or {})
        if method == "resources/list":
            return {"resources": self.b.resource_defs()}
        if method == "resources/templates/list":
            # The server advertises the resources capability, so a full
            # resources surface answers templates/list even with no templates
            # to offer. An error here is a warning on EVERY client connect
            # (measured: OpenCode logs "failed to list MCP resource
            # templates" at each session start); this server's resources are
            # static, so the honest answer is an empty list.
            return {"resourceTemplates": []}
        if method == "resources/read":
            uri = params.get("uri")
            if not uri:
                raise InvalidParams("missing resource uri")
            return self.b.read_resource(uri)
        # SEP-2640 (Skills extension): protocol-level methods, NOT tools.
        # The gate lives in the Bindings methods (same source,
        # self.skills_enabled, as the initialize advertisement); _route
        # only delegates, so a disabled gate surfaces as MethodNotFound.
        if method == "skills/list":
            return self.b.skills_list(params.get("cursor"))
        if method == "skills/get":
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                raise InvalidParams("missing skill uri")
            return self.b.skills_get(uri)
        if method == "resources/directory/read":
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                raise InvalidParams("missing resource uri")
            return self.b.skills_directory_read(uri, params.get("cursor"))
        raise MethodNotFound(f"unknown method '{method}'")

    def _initialize(self, params: dict) -> dict:
        if not isinstance(params, dict):
            params = {}
        hook = getattr(self.b, "on_initialize", None)
        if hook is not None:                 # v2.0.1: host manifest wiring
            hook(params)
        requested = params.get("protocolVersion")
        version = (requested if requested in SUPPORTED_PROTOCOLS
                   else SUPPORTED_PROTOCOLS[-1])
        capabilities: dict = {"tools": {"listChanged": True},
                              "resources": {}}
        if getattr(self.b, "skills_enabled", False):
            # SEP-2640 §Capability Declaration (SEP-2133 negotiation):
            # the extension lives in capabilities.extensions, and a server
            # declaring it MUST also declare the resources capability
            # (already declared above, {} suffices: the SEP sets no
            # content requirement on it). directoryRead is always true
            # when the extension is declared: this version has no state
            # with skills on and directory read off (single gate, see
            # CHANGELOG); a second flag would be needed to split them.
            capabilities["extensions"] = {
                "io.modelcontextprotocol/skills": {"directoryRead": True}}
        return {"protocolVersion": version,
                "serverInfo": {"name": "conscio", "version": self.b.version()},
                "capabilities": capabilities,
                "conscio": self.b.conscio_meta()}
