# conscio/agency/act.py
"""
ActPipeline — the volition loop, L1 PROPOSE (spec section 6).

reflect() stays untouched and passive; act() runs downstream consuming
the ConsciousnessState it produced. F1 stops at the validated proposal:
a human (or caller) finishes the cycle via approve()/reject(). The
Skeptic phase slots in between checks and approve in F2.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from conscio.context_manager import ConsciousnessState

from .actor import build_actor_prompt
from .adapter import InferenceAdapter
from .breaker import CircuitBreaker
from .contracts import PROPOSAL_SCHEMA, ActionProposal, ToolResult, validate
from .gateway import GatewayError, OutputGateway
from .ledger import ActionLedger
from .tools import ToolRegistry


class ActStatus(str, Enum):
    PROPOSED = "proposed"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"
    LOCKED = "locked"


@dataclass
class ActReport:
    status: ActStatus
    proposal: ActionProposal | None = None
    result: ToolResult | None = None
    ledger_id: int | None = None
    reason: str = ""
    lockdown: bool = False


def goal_fingerprint(goal_text: str) -> str:
    return hashlib.sha256(goal_text.encode("utf-8")).hexdigest()[:16]


class ActPipeline:
    def __init__(self, *, adapter: InferenceAdapter, registry: ToolRegistry,
                 ledger: ActionLedger, breaker: CircuitBreaker,
                 gateway: OutputGateway | None = None,
                 recall_fn: Callable[[str], list[str]] | None = None,
                 emit_fn: Callable[..., Any] | None = None,
                 few_shot_provider: Callable[[str], list[str]] | None = None):
        self.adapter = adapter
        self.registry = registry
        self.ledger = ledger
        self.breaker = breaker
        self.gateway = gateway or OutputGateway(adapter)
        self.recall_fn = recall_fn
        self.emit_fn = emit_fn or (lambda **kw: None)
        self.few_shot_provider = few_shot_provider

    # ── L1 PROPOSE ──

    def act(self, state: ConsciousnessState) -> ActReport:
        if state.action_lockdown:
            return ActReport(status=ActStatus.LOCKED,
                             reason="action_lockdown active")
        if not state.active_goals:
            return ActReport(status=ActStatus.FAILED,
                             reason="no active goals")
        goal_text = state.active_goals[0]      # GoalArbiter arrives in F3
        goal_fp = goal_fingerprint(goal_text)

        recall = self.recall_fn(goal_text) if self.recall_fn else []
        few_shot = (self.few_shot_provider(goal_text)
                    if self.few_shot_provider else [])
        prompt = build_actor_prompt(
            state=state, goal_text=goal_text,
            catalog_text=self.registry.catalog_text(),
            recall_snippets=recall, few_shot=few_shot)
        self.emit_fn(type="tool_call", category="external",
                     data={"action": "cycle_start", "goal_fp": goal_fp})

        try:
            proposal = self.gateway.request_action(
                prompt, PROPOSAL_SCHEMA, goal_id=goal_fp)
        except GatewayError as exc:
            return self._fail(goal_fp, tool="", args={},
                              reason=f"decode failed: {exc}")

        # deterministic checks (skeptic checks 1-2 — always code, no LLM)
        spec = self.registry.get(proposal.tool)
        if spec is None:
            return self._fail(goal_fp, tool=proposal.tool,
                              args=proposal.args,
                              reason=f"unknown tool '{proposal.tool}'")
        arg_errors = validate(proposal.args, spec.params)
        if arg_errors:
            return self._fail(goal_fp, tool=proposal.tool,
                              args=proposal.args,
                              reason="invalid args: " + "; ".join(arg_errors))

        row_id = self.ledger.record(
            goal_fp=goal_fp, tool=proposal.tool,
            args_json=json.dumps(proposal.args),
            rationale=proposal.rationale, tier="T2", status="proposed",
            adapter=type(self.adapter).__name__,
            model=self.adapter.capabilities().model_name)
        self.emit_fn(type="tool_call", category="external",
                     data={"action": "proposed", "tool": proposal.tool,
                           "goal_fp": goal_fp})
        return ActReport(status=ActStatus.PROPOSED, proposal=proposal,
                         ledger_id=row_id)

    # ── human gate ──

    def approve(self, ledger_id: int) -> ActReport:
        row = self.ledger.get(ledger_id)
        if row is None or row["status"] != "proposed":
            return ActReport(status=ActStatus.FAILED,
                             reason=f"no pending proposal #{ledger_id}")
        if self.registry.get(row["tool"]) is None:
            # registry changed between act() and approve()
            self.ledger.update_execution(
                ledger_id, ok=False, output="",
                error="tool no longer registered", duration_ms=0,
                status="failed")
            return ActReport(status=ActStatus.FAILED, ledger_id=ledger_id,
                             reason="tool no longer registered")
        result = self.registry.dispatch(row["tool"],
                                        json.loads(row["args_json"]))
        status = "executed" if result.ok else "failed"
        self.ledger.update_execution(
            ledger_id, ok=result.ok, output=result.output,
            error=result.error, duration_ms=result.duration_ms,
            status=status)
        self.emit_fn(type="tool_call", category="external",
                     data={"action": status, "tool": row["tool"]})
        return ActReport(
            status=ActStatus.EXECUTED if result.ok else ActStatus.FAILED,
            result=result, ledger_id=ledger_id,
            reason="" if result.ok else result.error)

    def reject(self, ledger_id: int, reason: str = "") -> None:
        self.ledger.update_execution(ledger_id, ok=False, output="",
                                     error=reason or "rejected",
                                     duration_ms=0, status="rejected")

    # ── failure path + breaker ──

    def _fail(self, goal_fp: str, *, tool: str, args: dict,
              reason: str) -> ActReport:
        self.ledger.record(goal_fp=goal_fp, tool=tool or "(none)",
                           args_json=json.dumps(args), rationale="",
                           tier="T2", status="failed")
        lockdown = False
        if self.breaker.should_trip(goal_fp):
            self.breaker.trip(goal_fp, detail=reason)
            lockdown = True
        return ActReport(status=ActStatus.FAILED, reason=reason,
                         lockdown=lockdown)
