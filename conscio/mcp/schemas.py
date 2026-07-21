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
    # ── v3.0 Gate tools ──────────────────────────────────────────────
    {"name": "conscio.decide",
     "description": "Create or update an Architecture Decision Record (ADR). "
                    "Creating: pass title + context. Updating: pass adr_id + status.",
     "inputSchema": {"type": "object",
                     "properties": {"title": {"type": "string"},
                                    "context": {"type": "string"},
                                    "alternatives": {"type": "array", "items": {"type": "string"}},
                                    "adr_id": {"type": "string"},
                                    "status": {"type": "string",
                                               "description": "proposed|accepted|deprecated|superseded"},
                                    "deciders": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "conscio.council",
     "description": "Convene a 4-voice council (architect, skeptic, pragmatist, critic) "
                    "for decision analysis. Returns votes and recommendation.",
     "inputSchema": {"type": "object",
                     "properties": {"question": {"type": "string"},
                                    "context": {"type": "string"},
                                    "options": {"type": "array", "items": {"type": "string"}}},
                     "required": ["question"]}},
    {"name": "conscio.loop_gate",
     "description": "Vet an autonomous loop against 4 conditions: frequency, "
                    "verifiable, budget_ok, has_tools. Returns approved/vetoed.",
     "inputSchema": {"type": "object",
                     "properties": {"task": {"type": "string"},
                                    "frequency": {"type": "string"},
                                    "verifiable": {"type": "boolean", "default": True},
                                    "budget_ok": {"type": "boolean", "default": True},
                                    "has_tools": {"type": "boolean", "default": True}}}},
    {"name": "conscio.delivery_check",
     "description": "Pre-close quality gate: rationalization patterns, stale proposals, "
                    "disk space. Also runs automatically on engine.close().",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "conscio.investigate",
     "description": "Verify that a target was read before acting. Checks EventBus "
                    "for investigate:read events mentioning the target.",
     "inputSchema": {"type": "object",
                     "properties": {"target": {"type": "string"},
                                    "action_type": {"type": "string"}},
                     "required": ["target"]}},
    # ── v3.0 Pipeline tools ───────────────────────────────────────────
    {"name": "conscio.acceptance_criteria",
     "description": "Generate acceptance criteria for a goal. Auto-detects risk "
                    "domains and depth (quick/full). Emits pipeline:acceptance event.",
     "inputSchema": {"type": "object",
                     "properties": {"goal": {"type": "string"},
                                    "depth": {"type": "string",
                                               "description": "quick|full|auto"},
                                    "risk_domains": {"type": "array",
                                                      "items": {"type": "string"}}}}},
    {"name": "conscio.verify",
     "description": "Verify acceptance criteria against evidence events. "
                    "Use criteria_source='acceptance' to load from last acceptance event.",
     "inputSchema": {"type": "object",
                     "properties": {"criteria": {"type": "array"},
                                    "criteria_source": {"type": "string"}}}},
    {"name": "conscio.continuous_loop",
     "description": "Select and gate an autonomous loop pattern (sequential, "
                    "continuous_pr, rfc_dag, infinite). Checks loop_gate conditions.",
     "inputSchema": {"type": "object",
                     "properties": {"task": {"type": "string"},
                                    "pattern": {"type": "string"},
                                    "frequency": {"type": "string"},
                                    "verifiable": {"type": "boolean", "default": True},
                                    "budget_ok": {"type": "boolean", "default": True},
                                    "has_tools": {"type": "boolean", "default": True}}}},
    {"name": "conscio.strategic_compact",
     "description": "Advise on strategic context compaction timing. "
                    "Checks token pressure and workflow phase.",
     "inputSchema": {"type": "object",
                     "properties": {"phase": {"type": "string"},
                                    "context_tokens": {"type": "integer"},
                                    "context_window": {"type": "integer"}}}},
    {"name": "conscio.ledger",
     "description": "Record, query, or promote entries in the recursive decision "
                    "ledger with coherence marks and promotion gates.",
     "inputSchema": {"type": "object",
                     "properties": {"action": {"type": "string",
                                                "description": "record|query|promote"},
                                    "rollout_id": {"type": "string"},
                                    "candidates": {"type": "array"},
                                    "fresh_info": {"type": "string"},
                                    "search_space_size": {"type": "integer"},
                                    "marks": {"type": "object"},
                                    "prior_winner": {"type": "string"},
                                    "coherence_mark": {"type": "object"}}}},
    # ── v3.0 Diagnostic tools ────────────────────────────────────────
    {"name": "conscio.context_budget",
     "description": "Audit context window consumption. Shows token pressure, "
                    "source breakdown, and optimization recommendations.",
     "inputSchema": {"type": "object",
                     "properties": {"context_tokens": {"type": "integer"},
                                    "context_window": {"type": "integer"},
                                    "detail": {"type": "string",
                                               "description": "summary|full"}}}},
    {"name": "conscio.eval_harness",
     "description": "Formal evaluation framework with pass@k metrics. "
                    "Define evals, record results, get reliability reports.",
     "inputSchema": {"type": "object",
                     "properties": {"action": {"type": "string",
                                                "description": "define|run|report"},
                                    "eval_id": {"type": "string"},
                                    "eval_type": {"type": "string"},
                                    "task": {"type": "string"},
                                    "criteria": {"type": "array", "items": {"type": "string"}},
                                    "results": {"type": "array", "items": {"type": "boolean"}},
                                    "k_values": {"type": "array", "items": {"type": "integer"}}}}},
    {"name": "conscio.rules_distill",
     "description": "Extract cross-cutting principles from skills and events. "
                    "Scan for patterns, distill into rules, list existing rules.",
     "inputSchema": {"type": "object",
                     "properties": {"action": {"type": "string",
                                                "description": "scan|distill|list"},
                                    "source_types": {"type": "array", "items": {"type": "string"}},
                                    "min_occurrences": {"type": "integer"},
                                    "rule_text": {"type": "string"},
                                    "rule_id": {"type": "string"}}}},
    {"name": "conscio.health",
     "description": "Quick health check — are all Conscio modules operational? "
                    "Returns mode, model, pending proposals, active goals, and "
                    "stale entity count. Pure read-only.",
     "inputSchema": {"type": "object",
                     "properties": {}}},
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
