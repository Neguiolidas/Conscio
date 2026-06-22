# conscio/agency/trial.py
"""Trial replay of a quarantined foreign skill (v2.2.2).

A pure mechanic: replay fixed foreign plan steps through the existing safety
stack (validate -> precheck -> HIGH-block -> Skeptic -> dispatch) against an
INJECTED sandboxed registry, stopping at the first failure. Records nothing,
imports nothing from conscio.noosphere or conscio.engine. The caller (the
engine) owns the sandbox lifecycle, the tamper guard, and persistence, and
guarantees trial isolation (no agent ledger/skills/trust/breaker writes)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import ActionProposal, validate
from .tools import Risk

# stage -> the word used in TrialOutcome.result
_RESULT_WORD = {
    "unknown_tool": "unknown_tool",
    "invalid_args": "invalid_args",
    "precheck": "precheck",
    "high_risk": "high_risk_blocked",
    "skeptic": "skeptic_reject",
    "exec": "exec_fail",
}


@dataclass(frozen=True)
class StepResult:
    tool: str
    ok: bool
    stage: str        # unknown_tool|invalid_args|precheck|high_risk|skeptic|exec|ok
    error: str = ""


@dataclass(frozen=True)
class TrialOutcome:
    passed: bool
    result: str           # "passed" or "<word>:<tool>"
    error: str
    steps: list[StepResult]   # always supplied explicitly by run_trial


@dataclass(frozen=True)
class TrialRefusal:
    reason: str


def run_trial(steps: list[dict], *, goal_text: str, skeptic: Any,
              registry: Any) -> TrialOutcome:
    done: list[StepResult] = []
    for step in steps:
        tool = str(step.get("tool", ""))
        args = step.get("args", {})
        rationale = str(step.get("rationale", ""))
        # Foreign steps carry no expected_outcome; the Skeptic tolerates "".
        proposal = ActionProposal(tool=tool, args=args, rationale=rationale,
                                  expected_outcome="")
        spec = registry.get(tool)
        if spec is None:
            done.append(StepResult(tool, False, "unknown_tool",
                                   f"tool '{tool}' not registered"))
            break
        errs = (validate(args, spec.params) if isinstance(args, dict)
                else ["args must be a dict"])
        if errs:
            done.append(StepResult(tool, False, "invalid_args",
                                   "; ".join(errs)))
            break
        if spec.precheck is not None:
            pre = spec.precheck(args)
            if pre:
                done.append(StepResult(tool, False, "precheck", str(pre)))
                break
        if spec.risk is Risk.HIGH:
            done.append(StepResult(tool, False, "high_risk",
                                   "HIGH-risk tool blocked in trial"))
            break
        verdict = skeptic.audit(proposal, goal_text=goal_text)   # forced audit
        if not verdict.passed:
            done.append(StepResult(tool, False, "skeptic",
                                   "; ".join(verdict.reasons)))
            break
        res = registry.dispatch(tool, args)
        if not res.ok:
            done.append(StepResult(tool, False, "exec", res.error))
            break
        done.append(StepResult(tool, True, "ok"))

    passed = bool(done) and len(done) == len(steps) and all(s.ok for s in done)
    if passed:
        return TrialOutcome(True, "passed", "", done)
    last = done[-1] if done else StepResult("", False, "empty", "no steps")
    word = _RESULT_WORD.get(last.stage, last.stage)
    result = f"{word}:{last.tool}" if last.tool else word
    return TrialOutcome(False, result, last.error, done)
