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

# feed also accepts the host's live context usage so the metabolic tier
# (ACTIVE/FATIGUE/CRITICAL) reflects reality — Conscio cannot measure the
# host's session length on its own.
_FEED_INPUT = {"type": "object",
               "properties": {
                   "event": {"type": "object"},
                   "session_tokens": {
                       "type": "integer",
                       "description": "Host's live context tokens used so far; "
                                      "drives the metabolic tier. Optional."}},
               "required": ["event"]}

BASE_TOOL_DEFS: list[dict] = [
    {"name": "conscio.feed",
     "description": "Ingest a perception Event; runs perceive+reflect; returns "
                    "the updated advisory. Idempotent on event.id. Pass "
                    "session_tokens to drive the metabolic tier.",
     "inputSchema": _FEED_INPUT},
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
    {"name": "conscio.state",
     "description": "ConsciousnessState snapshot (pure read).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "conscio.events",
     "description": "Recent events (pure read; type/category/since/limit).",
     "inputSchema": {"type": "object",
                     "properties": {"type": {"type": "string"},
                                    "category": {"type": "string"},
                                    "since": {"type": "string"},
                                    "limit": {"type": "integer"}}}},
    {"name": "conscio.handoff",
     "description": "Latest session handoff (pure read, markdown).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "conscio.structure",
     "description": "Report the workspace structural graph loaded into "
                    "awareness (consent-gated; data, never code). Returns the "
                    "distilled digest + counts, or loaded=false when none is "
                    "consented/loaded.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "conscio.structural_lookup",
     "description": "Resolve a structural node / hyperedge / community id from "
                    "the loaded graph to its detail; null on miss.",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string"}},
                     "required": ["key"]}},
    {"name": "conscio.cognitive_cycle",
     "description": "Run one explicit cognitive pass (reflect -> synthesize -> "
                    "propose/act -> learn -> self-improve) and return a report "
                    "of each stage. The act stage runs only when the server has "
                    "act enabled; otherwise propose-only. Pass session_tokens "
                    "to drive the metabolic tier.",
     "inputSchema": {"type": "object",
                     "properties": {"world_state": {"type": "string"},
                                    "session_tokens": {"type": "integer"}}}},
    {"name": "conscio.evaluate",
     "description": "5-axis self-evaluation scorecard (accuracy, completeness, "
                    "clarity, actionability, conciseness). Scores 1-5 with "
                    "concrete evidence. Pure read-only — no state mutation.",
     "inputSchema": {"type": "object",
                     "properties": {"task_description": {"type": "string",
                                                        "description": "what the agent was trying to do"},
                                    "output": {"type": "string",
                                               "description": "optional output text being evaluated "
                                                              "(used for conciseness and clarity heuristics)"}}}},
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

# ── v2.6.0 Liaison tool defs (exposed only when --enable-hermes-review) ───
_REVIEW_REJECT_INPUT = {"type": "object",
                        "properties": {"fp": {"type": "string"},
                                       "reason": {"type": "string"}},
                        "required": ["fp", "reason"]}
_FP_OPT_REASON = {"type": "object",
                  "properties": {"fp": {"type": "string"},
                                 "reason": {"type": "string"}},
                  "required": ["fp"]}
_LIMIT_ONLY = {"type": "object", "properties": {"limit": {"type": "integer"}}}

LIAISON_TOOL_DEFS: list[dict] = [
    {"name": "conscio.reviews",
     "description": "List inbound cross-agent review requests directed here "
                    "(reviewer role; deduped per fp).",
     "inputSchema": _LIMIT_ONLY},
    {"name": "conscio.review_approve",
     "description": "Send an approve verdict for a review request by fp.",
     "inputSchema": _FP_OPT_REASON},
    {"name": "conscio.review_reject",
     "description": "Send a reject verdict for a review request by fp.",
     "inputSchema": _REVIEW_REJECT_INPUT},
    {"name": "conscio.poll_reviews",
     "description": "Apply inbound verdicts from allowlisted reviewers to local "
                    "pending acts (proposer role); returns applied packets.",
     "inputSchema": _LIMIT_ONLY},
]

_RELAY_SEND_INPUT = {"type": "object",
                     "properties": {"to": {"type": "string"},
                                    "type": {"type": "string"},
                                    "payload": {"type": "object"}},
                     "required": ["to", "type", "payload"]}
_IDS_INPUT = {"type": "object",
              "properties": {"ids": {"type": "array",
                                     "items": {"type": "integer"}}},
              "required": ["ids"]}
_RELAY_BROADCAST_INPUT = {"type": "object",
                          "properties": {"type": {"type": "string"},
                                         "payload": {"type": "object"}},
                          "required": ["type", "payload"]}

RELAY_TOOL_DEFS: list[dict] = [
    {"name": "conscio.relay_send",
     "description": "Send a directed free-form message to a trusted relay peer.",
     "inputSchema": _RELAY_SEND_INPUT},
    {"name": "conscio.relay_inbox",
     "description": "Peek unread relay messages from trusted peers (review "
                    "types excluded).",
     "inputSchema": _LIMIT_ONLY},
    {"name": "conscio.relay_read",
     "description": "Mark relay messages consumed by id.",
     "inputSchema": _IDS_INPUT},
    {"name": "conscio.relay_broadcast",
     "description": "Send a free-form message to ALL trusted relay peers "
                    "(fan-out; review types excluded).",
     "inputSchema": _RELAY_BROADCAST_INPUT},
]
