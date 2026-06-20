# conscio/mcp/server.py
"""Engine-backed bindings (propose-only) + the stdio serve loop + the
conscio-mcp CLI. stdout is the protocol channel; logging goes to stderr."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from conscio import __version__
from conscio.engine import ConsciousnessEngine
from conscio.workspace import WorkspaceContext

from . import jsonrpc as j
from .protocol import SUPPORTED_PROTOCOLS, Dispatcher
from .schemas import (BASE_TOOL_DEFS, RESOURCE_DEFS, derive_event_id,
                      event_to_frame, validate_event)
from .seen import SeenStore


class Bindings:
    def __init__(self, engine: ConsciousnessEngine, seen: SeenStore, *,
                 adapter_name: str | None = None,
                 workspace_id: str = "") -> None:
        self.engine = engine
        self.seen = seen
        self.adapter_name = adapter_name
        self.workspace_id = workspace_id

    # ── discovery ──
    def version(self) -> str:
        return __version__

    def conscio_meta(self) -> dict:
        return {"workspace_id": self.workspace_id,
                "awake": bool(getattr(self.engine._state, "awake", False)),
                "act_enabled": False,           # v2.0.0: propose-only
                "adapter": self.adapter_name,
                "supported_protocols": SUPPORTED_PROTOCOLS}

    def tool_defs(self) -> list[dict]:
        return list(BASE_TOOL_DEFS)

    def resource_defs(self) -> list[dict]:
        return list(RESOURCE_DEFS)

    # ── tool dispatch ──
    def call_tool(self, name: str, args: dict) -> dict:
        fn = self._tools().get(name)
        if fn is None:
            raise j.MethodNotFound(f"tool '{name}' not available")
        return {"content": [{"type": "text", "text": json.dumps(fn(args))}]}

    def _tools(self):
        return {
            "conscio.feed": self._feed,
            "conscio.note": self._note,
            "conscio.advisory": lambda a: self.engine.advisory(),
            "conscio.recall": lambda a: {"snippets": self.engine.recall(
                self._require(a, "query"), int(a.get("k", 3)),
                a.get("categories"))},
            "conscio.propose_action": lambda a: self.engine.propose_action(
                self._require(a, "intent")),
            "conscio.propose_plan": lambda a: self.engine.propose_plan(
                self._require(a, "goal"), a.get("tools")),
        }

    @staticmethod
    def _require(args: dict, key: str):
        if key not in args:
            raise j.InvalidParams(f"missing '{key}'")
        return args[key]

    def _feed(self, args: dict) -> dict:
        event = self._require(args, "event")
        errors = validate_event(event)
        if errors:
            raise j.InvalidParams("; ".join(errors))
        eid = derive_event_id(event)
        prior = self.seen.seen(eid)
        if prior is not None:
            return json.loads(prior)
        world_state = event_to_frame(event).to_world_state()
        self.engine.perceive(world_state)
        self.engine.reflect(world_state)
        result = {"event_id": eid, "advisory": self.engine.advisory()}
        self.seen.mark(eid, json.dumps(result),
                       float(event.get("ts") or time.time()))
        return result

    def _note(self, args: dict) -> dict:
        event = self._require(args, "event")
        errors = validate_event(event)
        if errors:
            raise j.InvalidParams("; ".join(errors))
        eid = derive_event_id(event)
        prior = self.seen.seen(eid)
        if prior is not None:
            return json.loads(prior)
        self.engine.event_bus.emit(
            type="host:event", category="external",
            data={"host_type": event["type"],
                  "host_category": event["category"],
                  "source": event["source"],
                  "payload": event.get("payload", {}) or {}})
        result = {"event_id": eid, "noted": True}
        self.seen.mark(eid, json.dumps(result),
                       float(event.get("ts") or time.time()))
        return result

    # ── resources ──
    def read_resource(self, uri: str) -> dict:
        parsed = urlparse(uri)
        base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        if base == "conscio://advisory":
            return self._json_resource(uri, self.engine.advisory())
        if base == "conscio://state":
            return self._json_resource(uri,
                                       self.engine.advisory().get("state", {}))
        if base == "conscio://events":
            q = parse_qs(parsed.query)

            def first(key: str) -> str | None:
                vals = q.get(key)
                return vals[0] if vals else None

            rows = self.engine.event_bus.query(
                type=first("type"),
                category=first("category"),
                since=first("since"),
                limit=int(first("limit") or "50"))
            return self._json_resource(uri, [e.to_dict() for e in rows])
        if base == "conscio://handoff":
            return {"contents": [{"uri": uri, "mimeType": "text/markdown",
                                 "text": self._handoff_text()}]}
        raise j.InvalidParams(f"unknown resource '{uri}'")

    @staticmethod
    def _json_resource(uri: str, data: Any) -> dict:
        return {"contents": [{"uri": uri, "mimeType": "application/json",
                             "text": json.dumps(data)}]}

    def _handoff_text(self) -> str:
        path = Path(self.engine.storage) / "handoff" / "_session_handoff.md"
        try:
            return path.read_text()
        except OSError:
            return ""


def serve(bindings: Bindings, instream, outstream, *,
          max_bytes: int = j.DEFAULT_MAX_FRAME_BYTES) -> None:
    dispatcher = Dispatcher(bindings)
    for frame in j.read_frames(instream, max_bytes):
        if frame is j.OVERSIZE:
            _write(outstream, j.make_error(None, j.INVALID_REQUEST,
                                           "frame too large"))
            continue
        try:
            msg = json.loads(frame)
        except json.JSONDecodeError:
            _write(outstream, j.make_error(None, j.PARSE_ERROR, "parse error"))
            continue
        if not isinstance(msg, dict):
            _write(outstream, j.make_error(None, j.INVALID_REQUEST,
                                           "request must be an object"))
            continue
        response = dispatcher.handle(msg)
        if response is not None:
            _write(outstream, response)


def _write(outstream, obj: dict) -> None:
    outstream.write(json.dumps(obj) + "\n")
    outstream.flush()


def _build_adapter(spec: str):
    """spec forms: 'mock' | 'ollama:<model>'."""
    from conscio.agency import MockAdapter, OllamaAdapter
    if spec == "mock":
        return MockAdapter(script=[])
    if spec.startswith("ollama:"):
        return OllamaAdapter(model=spec.split(":", 1)[1])
    raise SystemExit(f"unsupported --adapter '{spec}'")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="conscio-mcp",
                                     description="Conscio MCP stdio server "
                                                 "(propose-only)")
    parser.add_argument("--storage", default=None)
    parser.add_argument("--model", default="glm-5.1")
    parser.add_argument("--adapter", default=None)
    parser.add_argument("--max-frame-bytes", type=int,
                        default=j.DEFAULT_MAX_FRAME_BYTES)
    parser.add_argument("--seen-max-rows", type=int, default=10_000)
    parser.add_argument("--seen-max-age-days", type=int, default=30)
    args = parser.parse_args(argv)

    engine = ConsciousnessEngine(args.model, storage_path=args.storage)
    adapter_name = None
    if args.adapter:
        engine.attach_adapter(_build_adapter(args.adapter))
        adapter_name = args.adapter
    workspace = WorkspaceContext().current()
    seen = SeenStore(Path(engine.storage) / "mcp_seen.db")
    seen.prune(args.seen_max_rows, args.seen_max_age_days)
    bindings = Bindings(engine, seen, adapter_name=adapter_name,
                        workspace_id=workspace.id)
    print(f"conscio-mcp {__version__} ready "
          f"(workspace={workspace.id}, mode=propose-only)", file=sys.stderr)
    try:
        serve(bindings, sys.stdin, sys.stdout, max_bytes=args.max_frame_bytes)
    finally:
        seen.close()
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
