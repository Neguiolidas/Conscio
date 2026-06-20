"""Conscio MCP — embodiment surface (v2.0 "Connect"). Propose-only (v2.0.0).

A hand-rolled, stdlib-only MCP stdio server. Zero new runtime dependency.
Nothing here opens a socket.
"""
from .server import main, serve

__all__ = ["main", "serve"]
