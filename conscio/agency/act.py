# conscio/agency/act.py
"""
ActPipeline — the volition loop (spec section 6). F2: immunity.

reflect() stays untouched and passive; act() runs downstream consuming
the ConsciousnessState it produced. The cycle: deterministic checks →
risk gating → Skeptic audit (clean call) → PROPOSED (L1 / HIGH risk) or
immediate supervised execution (L2, earned via TrustMatrix). Skeptic
FAIL is recorded as a failed row (feeds the breaker); a human reject()
stays 'rejected' and never counts against the agent.

F3: GoalArbiter selection, real decode tier in the ledger,
profile-driven tool visibility (max_visible_tools).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from conscio.context_manager import ConsciousnessState

from conscio.prompt_zones import build_zoned_prompt
from .adapter import InferenceAdapter
from .breaker import CircuitBreaker
from .contracts import (PROPOSAL_SCHEMA, ActionProposal, AuditVerdict,
                        ToolResult, validate)
from .fingerprint import goal_fingerprint
from .gateway import GatewayError, OutputGateway
from .ledger import ActionLedger
from .skeptic import Skeptic
from .tools import Risk, ToolRegistry
from .trust import TrustMatrix


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
    verdict: AuditVerdict | None = None
    result: ToolResult | None = None
    ledger_id: int | None = None
    reason: str = ""
    lockdown: bool = False


class ActPipeline:
    def __init__(self, *, adapter: InferenceAdapter, registry: ToolRegistry,
                 ledger: ActionLedger, breaker: CircuitBreaker,
                 gateway: OutputGateway | None = None,
                 skeptic: Skeptic | None = None,
                 trust: TrustMatrix | None = None,
                 meta: Any = None,
                 autonomy_cap: int = 1,
                 recall_fn: Callable[[str], list[str]] | None = None,
                 emit_fn: Callable[..., Any] | None = None,
                 few_shot_provider: Callable[[str], list[str]] | None = None,
                 arbiter: Any = None,
                 executable_fn: Callable[[str], bool] | None = None,
                 intercept_enabled: bool = False,
                 skill_summary_fn: Callable[[], str] | None = None):
        from .loop import GoalArbiter      # runtime: loop imports this module

        self.adapter = adapter
        self.registry = registry
        self.ledger = ledger
        self.breaker = breaker
        self.gateway = gateway or OutputGateway(adapter)
        self.skeptic = skeptic
        self.trust = trust
        self.meta = meta
        self.autonomy_cap = autonomy_cap
        self.recall_fn = recall_fn
        self.emit_fn = emit_fn or (lambda **kw: None)
        self.few_shot_provider = few_shot_provider
        self.arbiter = arbiter or GoalArbiter(breaker,
                                              executable_fn=executable_fn)
        self.max_visible_tools: int | None = None    # set by engine.probe()
        self.intercept_enabled = intercept_enabled
        self.skill_summary_fn = skill_summary_fn
        self.prompt_complexity: str = "full"  # set by engine.probe()

    # ── act cycle (spec §6) ──

    def act(self, state: ConsciousnessState) -> ActReport:
        if state.action_lockdown:
            return ActReport(status=ActStatus.LOCKED,
                             reason="action_lockdown active")
        if not state.active_goals:
            return ActReport(status=ActStatus.FAILED,
                             reason="no active goals")

        goal_text = self.arbiter.choose(state)
        if goal_text is None:
            # No goal the actor may run: every active goal is either quarantined
            # (breaker) or diagnostic-only (v1.6 #7 provenance gate).
            return ActReport(
                status=ActStatus.FAILED,
                reason="no executable goal (all quarantined or diagnostic-only)")
        goal_fp = goal_fingerprint(goal_text)

        recall = self.recall_fn(goal_text) if self.recall_fn else []
        few_shot = (self.few_shot_provider(goal_text)
                    if self.few_shot_provider else [])
        skill_summary = self.skill_summary_fn() if self.skill_summary_fn else None
        prompt = build_zoned_prompt(
            state=state, goal_text=goal_text,
            catalog_text=self.registry.catalog_text(self.max_visible_tools),
            recall_snippets=recall, few_shot=few_shot,
            intercept_enabled=self.intercept_enabled,
            skill_summary=skill_summary,
            complexity=self.prompt_complexity)
        self.emit_fn(type="tool_call", category="external",
                     data={"action": "cycle_start", "goal_fp": goal_fp})

        try:
            proposal = self.gateway.request_action(
                prompt, PROPOSAL_SCHEMA, goal_id=goal_fp,
                tool_names=self.registry.names())
        except GatewayError as exc:
            return self._fail(goal_fp, tool="", args={},
                              reason=f"decode failed: {exc}",
                              goal_text=goal_text)

        # deterministic checks (skeptic checks 1-2 + sandbox — no LLM)
        spec = self.registry.get(proposal.tool)
        if spec is None:
            return self._fail(goal_fp, tool=proposal.tool,
                              args=proposal.args,
                              reason=f"unknown tool '{proposal.tool}'",
                              goal_text=goal_text)
        arg_errors = validate(proposal.args, spec.params)
        if arg_errors:
            return self._fail(goal_fp, tool=proposal.tool,
                              args=proposal.args,
                              reason="invalid args: " + "; ".join(arg_errors),
                              goal_text=goal_text)
        if spec.precheck is not None:
            precheck_error = spec.precheck(proposal.args)
            if precheck_error:
                return self._fail(goal_fp, tool=proposal.tool,
                                  args=proposal.args,
                                  reason=f"precheck: {precheck_error}",
                                  goal_text=goal_text)

        # risk gating + semantic audit (spec §5.6)
        verdict = self._audit(spec, proposal, goal_text)
        if not verdict.passed:
            if self.meta is not None:
                self.meta.record_error(f"act:{proposal.tool}:skeptic_fail")
            return self._fail(goal_fp, tool=proposal.tool,
                              args=proposal.args,
                              reason="skeptic: " + "; ".join(verdict.reasons),
                              verdict=verdict, goal_text=goal_text,
                              report_status=ActStatus.REJECTED,
                              proposal=proposal)

        row_id = self.ledger.record(
            goal_fp=goal_fp, goal_text=goal_text, tool=proposal.tool,
            args_json=json.dumps(proposal.args),
            rationale=proposal.rationale,
            tier=self.gateway.last_tier or "T2", status="proposed",
            adapter=getattr(self.adapter, "wrapped_name",
                            type(self.adapter).__name__),
            model=self.adapter.capabilities().model_name)
        self.ledger.update_verdict(
            row_id, "PASS" if verdict.audited else "unaudited",
            verdict.reasons)
        self.emit_fn(type="tool_call", category="external",
                     data={"action": "proposed", "tool": proposal.tool,
                           "goal_fp": goal_fp})

        # HIGH risk never auto-executes (R6); L2 must be earned AND allowed
        if spec.risk is Risk.HIGH or self._effective_autonomy(
                proposal.tool) < 2:
            return ActReport(status=ActStatus.PROPOSED, proposal=proposal,
                             verdict=verdict, ledger_id=row_id)

        # L2 SUPERVISED: execute now, under the verdict just earned
        return self._execute(row_id, proposal, verdict, goal_fp, goal_text)

    # ── audit + autonomy helpers ──

    # v3.1: tools with no external side effects — skeptic audit is overkill.
    # think: pure reasoning, no I/O. memory_note: append-only local note.
    # fs_read excluded: it accesses filesystem (side-channel info leak risk).
    _SKEPTIC_SKIP_TOOLS = frozenset({"think", "memory_note"})

    def _audit(self, spec, proposal: ActionProposal,
               goal_text: str) -> AuditVerdict:
        # v3.1: skip skeptic for inherently safe tools (no side effects)
        if proposal.tool in self._SKEPTIC_SKIP_TOOLS:
            return AuditVerdict(
                verdict="PASS", audited=False, reasons=[],
                confidence=0.85,
                risk_flags=["skip:safe_tool"])
        if (spec.risk is Risk.LOW and self.trust is not None
                and self.trust.fast_path_ok()):
            return AuditVerdict(
                verdict="PASS", audited=False, reasons=[],
                confidence=self.trust.meta.calibration_score()
                if getattr(self.trust, "meta", None) is not None else 0.75)
        if self.skeptic is None:               # F1 wiring: no audit available
            return AuditVerdict(verdict="PASS", audited=False)
        return self.skeptic.audit(proposal, goal_text=goal_text)

    def _effective_autonomy(self, task_type: str) -> int:
        earned = (self.trust.autonomy_level(task_type)
                  if self.trust is not None else 1)
        return min(self.autonomy_cap, earned)

    def _execute(self, row_id: int, proposal: ActionProposal,
                 verdict: AuditVerdict, goal_fp: str,
                 goal_text: str) -> ActReport:
        result = self.registry.dispatch(proposal.tool, proposal.args)
        status = "executed" if result.ok else "failed"
        self.ledger.update_execution(
            row_id, ok=result.ok, output=result.output,
            error=result.error, duration_ms=result.duration_ms,
            status=status)
        self.emit_fn(type="tool_call", category="external",
                     data={"action": status, "tool": proposal.tool})
        if self.meta is not None:
            self.meta.record_confidence(
                proposal.tool, verdict.confidence,
                "success" if result.ok else "failure")
        lockdown = False
        if result.ok:
            if self.trust is not None:
                self.trust.on_success(proposal.tool)
        else:
            if self.meta is not None:
                self.meta.record_error(f"act:{proposal.tool}:exec_fail")
            if self.breaker.should_trip(goal_fp, task_type=proposal.tool):
                self.breaker.trip(goal_fp, detail=result.error,
                                  goal_text=goal_text)
                lockdown = self.breaker.global_lockdown_due()
        return ActReport(
            status=ActStatus.EXECUTED if result.ok else ActStatus.FAILED,
            proposal=proposal, verdict=verdict, result=result,
            ledger_id=row_id, reason="" if result.ok else result.error,
            lockdown=lockdown)

    # ── human gate ──

    def approve(self, ledger_id: int) -> ActReport:
        row = self.ledger.get(ledger_id)
        if row is None:
            return ActReport(status=ActStatus.FAILED,
                             reason=f"no pending proposal #{ledger_id}")
        # The atomic claim (proposed -> executing) is the SOLE gate: only the
        # winner dispatches, so a concurrent or repeated approve() can never
        # double-execute. A non-proposed row (already executed/rejected, or
        # claimed by a racing caller) loses the claim and is reported handled.
        if not self.ledger.claim(ledger_id):
            return ActReport(status=ActStatus.FAILED, ledger_id=ledger_id,
                             reason=f"proposal #{ledger_id} already handled")
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
        if self.meta is not None:
            self.meta.record_confidence(
                row["tool"], 0.5,                  # human-gated: neutral conf
                "success" if result.ok else "failure")
        if result.ok and self.trust is not None:
            self.trust.on_success(row["tool"])
        return ActReport(
            status=ActStatus.EXECUTED if result.ok else ActStatus.FAILED,
            result=result, ledger_id=ledger_id,
            reason="" if result.ok else result.error)

    def reject(self, ledger_id: int, reason: str = "") -> None:
        row = self.ledger.get(ledger_id)
        if row is None or row["status"] != "proposed":
            return                              # audit rows are immutable (R8)
        self.ledger.update_execution(ledger_id, ok=False, output="",
                                     error=reason or "rejected",
                                     duration_ms=0, status="rejected")

    # ── failure path + breaker ──

    def _fail(self, goal_fp: str, *, tool: str, args: dict, reason: str,
              verdict: AuditVerdict | None = None, goal_text: str = "",
              report_status: ActStatus = ActStatus.FAILED,
              proposal: ActionProposal | None = None) -> ActReport:
        row_id = self.ledger.record(goal_fp=goal_fp, goal_text=goal_text,
                                    tool=tool or "(none)",
                                    args_json=json.dumps(args), rationale="",
                                    tier=self.gateway.last_tier or "T2",
                                    status="failed")
        if verdict is not None:
            self.ledger.update_verdict(row_id, verdict.verdict,
                                       verdict.reasons)
        lockdown = False
        if self.breaker.should_trip(goal_fp, task_type=tool or ""):
            self.breaker.trip(goal_fp, detail=reason, goal_text=goal_text)
            lockdown = self.breaker.global_lockdown_due()
        return ActReport(status=report_status, proposal=proposal,
                         verdict=verdict, ledger_id=row_id, reason=reason,
                         lockdown=lockdown)
