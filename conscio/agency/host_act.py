# conscio/agency/host_act.py
"""HostActChannel — the host-executed audited-action state machine (v2.0.1).

Conscio audits + gates + ledgers and returns an execution packet; the HOST
executes and reports back via report(). Conscio never dispatches a host tool.
Reuses the engine's existing ActionLedger / Skeptic / CircuitBreaker /
TrustMatrix; takes a host-owned ToolRegistry (registry_from_manifest)."""
from __future__ import annotations

import json
from typing import Any, Callable

from ..risk import Risk
from .act import goal_fingerprint
from .contracts import PROPOSAL_SCHEMA, proposal_from_dict, validate

_PENDING_POLICIES = {"require_approval", "hermes_review"}


class HostActChannel:
    def __init__(self, *, ledger: Any, skeptic: Any, breaker: Any, trust: Any,
                 registry: Any, emit_fn: Callable[..., Any],
                 awake_fn: Callable[[], bool]) -> None:
        self.ledger = ledger
        self.skeptic = skeptic
        self.breaker = breaker
        self.trust = trust
        self.registry = registry
        self.emit_fn = emit_fn
        self.awake_fn = awake_fn

    # ── gate ──
    def _gate(self) -> dict | None:
        if not self.awake_fn():
            return {"status": "gated", "reason": "engine not awake"}
        if self.breaker.global_lockdown_due():
            return {"status": "gated", "reason": "action lockdown"}
        return None

    def _reject(self, intent: dict, reasons: list[str],
                risk_flags: list[str] | None = None,
                verdict: str = "FAIL") -> dict:
        rid = self.ledger.record(
            goal_fp=goal_fingerprint(str(intent.get("goal", ""))),
            goal_text=str(intent.get("goal", "")),
            tool=str(intent.get("tool", "(none)")),
            args_json=json.dumps(intent.get("args", {})),
            rationale=str(intent.get("rationale", "")), tier="host",
            status="failed")
        self.ledger.update_verdict(rid, verdict, reasons)
        return {"status": "rejected", "ledger_id": rid, "verdict": verdict,
                "reasons": reasons, "risk_flags": risk_flags or []}

    # ── propose ──
    def propose(self, intent: dict) -> dict:
        gated = self._gate()
        if gated:
            return gated
        errors = validate(intent, PROPOSAL_SCHEMA)
        if errors:
            return self._reject(intent, errors)
        spec = self.registry.get(intent["tool"])
        if spec is None:
            return self._reject(intent, [f"unknown tool '{intent['tool']}'"])
        arg_errors = validate(intent["args"], spec.params)
        if arg_errors:
            return self._reject(intent,
                                ["invalid args: " + "; ".join(arg_errors)])
        if spec.precheck is not None:
            pre = spec.precheck(intent["args"])
            if pre:
                return self._reject(intent, [f"precheck: {pre}"])

        goal = str(intent.get("goal", ""))
        proposal = proposal_from_dict(intent, goal_id=goal)
        verdict = self.skeptic.audit(proposal, goal_text=goal)
        if not verdict.passed:
            return self._reject(intent, verdict.reasons, verdict.risk_flags)

        rid = self.ledger.record(
            goal_fp=goal_fingerprint(goal), goal_text=goal, tool=proposal.tool,
            args_json=json.dumps(proposal.args), rationale=proposal.rationale,
            tier="host", status="proposed", approval_policy=spec.approval_policy)
        self.ledger.update_verdict(rid, verdict.verdict, verdict.reasons)
        self.emit_fn(type="proposal:audited", category="consciousness",
                     data={"tool": proposal.tool, "args": proposal.args,
                           "verdict": verdict.verdict, "host": True,
                           "ledger_id": rid})

        if spec.risk is Risk.HIGH or spec.approval_policy in _PENDING_POLICIES:
            return {"status": "pending_approval", "ledger_id": rid,
                    "verdict": "PASS", "risk": spec.risk.value,
                    "approval_policy": spec.approval_policy}

        # auto-release (LOW/MED + auto); breaker already cleared in _gate
        self.ledger.claim(rid)
        return {"status": "executable", "ledger_id": rid, "verdict": "PASS",
                "confidence": verdict.confidence,
                "packet": {"tool": proposal.tool, "args": proposal.args,
                           "ledger_id": rid}}

    # ── approve / reject (the high-risk gate) ──
    def approve(self, ledger_id: int) -> dict:
        row = self.ledger.get(ledger_id)
        if row is None or row["status"] != "proposed":
            return {"ok": False, "reason": "already_handled"}
        gated = self._gate()
        if gated:
            return gated
        if not self.ledger.claim(ledger_id):
            return {"ok": False, "reason": "already_handled"}
        return {"status": "executable", "ledger_id": ledger_id,
                "packet": {"tool": row["tool"],
                           "args": json.loads(row["args_json"]),
                           "ledger_id": ledger_id}}

    def reject(self, ledger_id: int, reason: str = "") -> dict:
        row = self.ledger.get(ledger_id)
        if row is None or row["status"] != "proposed":
            return {"ok": False, "reason": "already_handled"}
        self.ledger.update_execution(ledger_id, ok=False, output="",
                                     error=reason or "rejected",
                                     duration_ms=0, status="rejected")
        return {"ok": True, "status": "rejected", "ledger_id": ledger_id}
