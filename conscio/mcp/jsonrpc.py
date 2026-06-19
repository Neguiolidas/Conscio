# conscio/mcp/jsonrpc.py
"""JSON-RPC 2.0 framing for the MCP stdio transport. Engine-free + pure.

The reader is bounded AT THE SOURCE via readline(max+1) — never an
unbounded `for line in stream` (the B-008 unbounded-buffer lesson). A line
that hits the cap before its newline is drained + discarded and surfaced as
the OVERSIZE sentinel, so nothing oversized is ever held in memory.
"""
from __future__ import annotations

from typing import Any, Iterator

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

DEFAULT_MAX_FRAME_BYTES = 1_048_576     # 1 MiB

OVERSIZE = object()                      # sentinel yielded for a capped line


class InvalidParams(Exception):
    """A handler rejected its arguments (mapped to INVALID_PARAMS)."""


class MethodNotFound(Exception):
    """A method/tool is unavailable (mapped to METHOD_NOT_FOUND)."""


def make_response(id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def make_error(id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": err}


def _drain_to_newline(stream, cap: int) -> None:
    while True:
        chunk = stream.readline(cap)
        if chunk == "" or chunk.endswith("\n"):
            return


def read_frames(stream, max_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> Iterator:
    """Yield each complete line (newline-stripped, non-blank) as a str, or the
    OVERSIZE sentinel for a line that exceeded max_bytes before its newline."""
    while True:
        line = stream.readline(max_bytes + 1) if max_bytes else stream.readline()
        if line == "":
            return
        if max_bytes and len(line) > max_bytes and not line.endswith("\n"):
            _drain_to_newline(stream, max_bytes + 1)
            yield OVERSIZE
            continue
        stripped = line.rstrip("\n")
        if stripped.strip():
            yield stripped
