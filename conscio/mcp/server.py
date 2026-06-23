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
from .schemas import (ACT_TOOL_DEFS, BASE_TOOL_DEFS, RESOURCE_DEFS,
                      derive_event_id, event_to_frame, validate_event)
from .seen import SeenStore


class Bindings:
    def __init__(self, engine: ConsciousnessEngine, seen: SeenStore, *,
                 adapter_name: str | None = None,
                 workspace_id: str = "", act_flag: bool = False) -> None:
        self.engine = engine
        self.seen = seen
        self.adapter_name = adapter_name
        self.workspace_id = workspace_id
        self.act_flag = act_flag             # v2.0.1: --enable-act
        self._act_error = ""

    # ── discovery ──
    def version(self) -> str:
        return __version__

    def on_initialize(self, params: dict) -> None:
        """v2.0.1: read the host tool manifest from initialize params and enable
        act. Independent of session init — a bad/missing manifest never
        half-enables act (act stays off, act_error set)."""
        self._act_error = ""
        if not self.act_flag:
            return
        tools = (params.get("conscio") or {}).get("tools")
        if not tools:
            self._act_error = "no tool manifest in initialize params"
            return
        if not self.engine.enable_host_act(tools):
            self._act_error = self.engine.host_act_error or "act not enabled"

    def _act_enabled(self) -> bool:
        return self.act_flag and self.engine.host_act is not None

    def conscio_meta(self) -> dict:
        ha = self.engine.host_act
        return {"workspace_id": self.workspace_id,
                "awake": bool(self.engine.awake),
                "act_enabled": self._act_enabled(),
                "act_error": self._act_error,
                "host_tools_count": len(ha.registry.names()) if ha else 0,
                "adapter_ready": self.adapter_name is not None,
                "manifest_hash": getattr(self.engine, "_host_act_hash", ""),
                "adapter": self.adapter_name,
                "supported_protocols": SUPPORTED_PROTOCOLS}

    def tool_defs(self) -> list[dict]:
        defs = list(BASE_TOOL_DEFS)
        if self._act_enabled():
            defs += list(ACT_TOOL_DEFS)
        return defs

    def resource_defs(self) -> list[dict]:
        return list(RESOURCE_DEFS)

    # ── tool dispatch ──
    def call_tool(self, name: str, args: dict) -> dict:
        fn = self._tools().get(name)
        if fn is None:
            raise j.MethodNotFound(f"tool '{name}' not available")
        return {"content": [{"type": "text", "text": json.dumps(fn(args))}]}

    def _tools(self):
        tools = {
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
            "conscio.state": lambda a: self._state_payload(),
            "conscio.events": lambda a: self._events_payload(a),
            "conscio.handoff": lambda a: self._handoff_payload(),
        }
        if self._act_enabled():
            tools.update({
                "conscio.act": self._act,
                "conscio.report_result": self._report_result,
                "conscio.pending": lambda a: self.engine.host_act.pending(
                    self._int_arg(a, "limit", 20)),
                "conscio.approve": lambda a: self.engine.host_act.approve(
                    self._int_arg(a, "ledger_id")),
                "conscio.reject": lambda a: self.engine.host_act.reject(
                    self._int_arg(a, "ledger_id"), str(a.get("reason", ""))),
            })
        return tools

    def _int_arg(self, args: dict, key: str,
                 default: int | None = None) -> int:
        if key not in args:
            if default is not None:
                return default
            raise j.InvalidParams(f"missing '{key}'")
        val = args[key]
        if isinstance(val, bool) or not isinstance(val, int):  # not str/float/None
            raise j.InvalidParams(f"'{key}' must be an integer")
        return val

    def _report_result(self, args: dict) -> dict:
        ledger_id = self._int_arg(args, "ledger_id")
        result = self._require(args, "result")
        if not isinstance(result, dict):
            raise j.InvalidParams("result must be an object")
        return self.engine.host_act.report(ledger_id, result)

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

    def _act(self, args: dict) -> dict:
        intent = self._require(args, "intent")
        if not isinstance(intent, dict):
            raise j.InvalidParams("intent must be an object")
        key = intent.get("idempotency_key")
        skey = None
        if key is not None:
            if not isinstance(key, str) or len(key) > 256:
                raise j.InvalidParams("idempotency_key must be a str <= 256 chars")
            skey = f"act:{self.workspace_id}:{intent.get('tool', '')}:{key}"
            prior = self.seen.seen(skey)
            if prior is not None:
                return json.loads(prior)
        result = self.engine.host_act.propose(intent)
        if skey is not None:
            self.seen.mark(skey, json.dumps(result), time.time())
        return result

    # ── read-only state payloads (shared by resources + tools, v2.4) ──
    def _state_payload(self) -> dict:
        return self.engine.advisory().get("state", {})

    def _events_payload(self, params: dict) -> list[dict]:
        def s(key: str) -> str | None:
            v = params.get(key)
            return str(v) if v not in (None, "") else None
        try:
            limit = int(params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        rows = self.engine.event_bus.query(
            type=s("type"), category=s("category"), since=s("since"), limit=limit)
        return [e.to_dict() for e in rows]

    def _handoff_payload(self) -> str:
        return self._handoff_text()

    # ── resources ──
    def read_resource(self, uri: str) -> dict:
        parsed = urlparse(uri)
        base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        if base == "conscio://advisory":
            return self._json_resource(uri, self.engine.advisory())
        if base == "conscio://state":
            return self._json_resource(uri, self._state_payload())
        if base == "conscio://events":
            q = parse_qs(parsed.query)
            params = {k: (v[0] if v else None) for k, v in q.items()}
            return self._json_resource(uri, self._events_payload(params))
        if base == "conscio://handoff":
            return {"contents": [{"uri": uri, "mimeType": "text/markdown",
                                 "text": self._handoff_payload()}]}
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


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="conscio-mcp",
                                     description="Conscio MCP stdio server")
    parser.add_argument("--storage", default=None)
    parser.add_argument("--model", default="glm-5.1")
    parser.add_argument("--adapter", default=None)
    parser.add_argument("--max-frame-bytes", type=int,
                        default=j.DEFAULT_MAX_FRAME_BYTES)
    parser.add_argument("--seen-max-rows", type=int, default=10_000)
    parser.add_argument("--seen-max-age-days", type=int, default=30)
    parser.add_argument("--enable-act", action="store_true",
                        help="enable opt-in host-executed audited act")
    parser.add_argument("--awake", action="store_true",
                        help="set the engine Awake at startup (act gate)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _arg_parser().parse_args(argv)
    engine = ConsciousnessEngine(args.model, storage_path=args.storage)
    from conscio.adapter_config import build_adapter_from_config, load_config
    adapter_name = None
    if args.adapter:                               # CLI wins (mock | ollama:..)
        engine.attach_adapter(_build_adapter(args.adapter))
        adapter_name = args.adapter
    else:
        adapter, atype = build_adapter_from_config(load_config(),
                                                   fallback_model=args.model)
        if adapter is not None:
            engine.attach_adapter(adapter)
            adapter_name = atype
    if args.awake:
        engine.wake()                              # reuse the existing R9 toggle
    workspace = WorkspaceContext().current()
    seen = SeenStore(Path(engine.storage) / "mcp_seen.db")
    seen.prune(args.seen_max_rows, args.seen_max_age_days)
    bindings = Bindings(engine, seen, adapter_name=adapter_name,
                        workspace_id=workspace.id, act_flag=args.enable_act)
    mode = "act" if args.enable_act else "propose-only"
    print(f"conscio-mcp {__version__} ready "
          f"(workspace={workspace.id}, mode={mode})", file=sys.stderr)
    try:
        serve(bindings, sys.stdin, sys.stdout, max_bytes=args.max_frame_bytes)
    finally:
        seen.close()
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
