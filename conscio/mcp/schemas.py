# conscio/mcp/schemas.py
"""Rigid Event schema, perception mapping, idempotency keys, and the MCP
tool/resource definition dicts (tools/list + resources/list). Propose-only;
act/manifest defs are deferred to v2.0.1."""
from __future__ import annotations

import hashlib
import json

from conscio.agency.contracts import validate
from conscio.perception import PerceptionFrame

EVENT_SCHEMA: dict[str, dict] = {
    "type": {"type": "str", "required": True, "non_empty": True},
    "source": {"type": "str", "required": True, "non_empty": True},
    "category": {"type": "str", "required": True, "non_empty": True},
    "payload": {"type": "dict", "required": True},
    # "id" optional (derived when absent); "ts" optional (server stamps)
}


def validate_event(event: object) -> list[str]:
    return validate(event, EVENT_SCHEMA)


def event_to_frame(event: dict) -> PerceptionFrame:
    payload = event.get("payload", {}) or {}
    observations: list[str] = []
    signals: dict[str, float] = {}
    for key, value in payload.items():
        if isinstance(value, bool):
            observations.append(f"{key}={value}")
        elif isinstance(value, (int, float)):
            signals[key] = float(value)
        else:
            observations.append(f"{key}: {value}")
    return PerceptionFrame(
        source=f"{event['category']}:{event['source']}",
        observations=observations, signals=signals,
        ts=float(event.get("ts", 0.0) or 0.0))


def derive_event_id(event: dict) -> str:
    explicit = event.get("id")
    if explicit:
        return str(explicit)
    basis = json.dumps(
        {k: event.get(k) for k in ("type", "source", "category", "ts",
                                   "payload")}, sort_keys=True, default=str)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


_EVENT_INPUT = {"type": "object", "properties": {"event": {"type": "object"}},
                "required": ["event"]}

BASE_TOOL_DEFS: list[dict] = [
    {"name": "conscio.feed",
     "description": "Ingest a perception Event; runs perceive+reflect; returns "
                    "the updated advisory. Idempotent on event.id.",
     "inputSchema": _EVENT_INPUT},
    {"name": "conscio.note",
     "description": "Record a raw Event to the event log (no reflect). "
                    "Idempotent on event.id.",
     "inputSchema": _EVENT_INPUT},
    {"name": "conscio.advisory",
     "description": "Current cognitive state (pure read).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "conscio.recall",
     "description": "Retrieve relevant past context (FTS5 + RAG).",
     "inputSchema": {"type": "object",
                     "properties": {"query": {"type": "string"},
                                    "k": {"type": "integer"},
                                    "categories": {"type": "array"}},
                     "required": ["query"]}},
    {"name": "conscio.propose_action",
     "description": "Audit an explicit action intent (Skeptic). Never "
                    "executes. Returns verdict PASS/FAIL + reasons.",
     "inputSchema": {"type": "object",
                     "properties": {"intent": {"type": "object"}},
                     "required": ["intent"]}},
    {"name": "conscio.propose_plan",
     "description": "Generate ONE audited action from a goal (Actor), "
                    "constrained to the declared tool vocabulary. Never "
                    "executes; not multi-step; not free-form.",
     "inputSchema": {"type": "object",
                     "properties": {"goal": {"type": "string"},
                                    "tools": {"type": "array"}},
                     "required": ["goal", "tools"]}},
]

RESOURCE_DEFS: list[dict] = [
    {"uri": "conscio://advisory", "name": "advisory",
     "description": "Current cognitive advisory", "mimeType": "application/json"},
    {"uri": "conscio://state", "name": "state",
     "description": "ConsciousnessState snapshot", "mimeType": "application/json"},
    {"uri": "conscio://events", "name": "events",
     "description": "Recent events (supports ?type=&category=&since=&limit=)",
     "mimeType": "application/json"},
    {"uri": "conscio://handoff", "name": "handoff",
     "description": "Latest session handoff", "mimeType": "text/markdown"},
]

# ── v2.0.1 Full Act tool defs (exposed only when --enable-act) ───────────

_INTENT_INPUT = {"type": "object",
                 "properties": {"intent": {"type": "object"}},
                 "required": ["intent"]}
_LEDGER_INPUT = {"type": "object",
                 "properties": {"ledger_id": {"type": "integer"}},
                 "required": ["ledger_id"]}

ACT_TOOL_DEFS: list[dict] = [
    {"name": "conscio.act",
     "description": "Audit a concrete action intent; if act is enabled + awake, "
                    "ledger it and return an executable packet or a pending "
                    "approval. The HOST executes; Conscio never does.",
     "inputSchema": _INTENT_INPUT},
    {"name": "conscio.report_result",
     "description": "Report a host execution outcome to close the ledger entry "
                    "and emit act:result.",
     "inputSchema": {"type": "object",
                     "properties": {"ledger_id": {"type": "integer"},
                                    "result": {"type": "object"}},
                     "required": ["ledger_id", "result"]}},
    {"name": "conscio.pending",
     "description": "List proposals awaiting approval (R6 queue).",
     "inputSchema": {"type": "object",
                     "properties": {"limit": {"type": "integer"}}}},
    {"name": "conscio.approve",
     "description": "Approve a pending proposal; returns an executable packet.",
     "inputSchema": _LEDGER_INPUT},
    {"name": "conscio.reject",
     "description": "Reject a pending proposal.",
     "inputSchema": {"type": "object",
                     "properties": {"ledger_id": {"type": "integer"},
                                    "reason": {"type": "string"}},
                     "required": ["ledger_id"]}},
]
