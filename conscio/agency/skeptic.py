# conscio/agency/skeptic.py
"""
Skeptic phase — semantic immunity (spec section 5.6 / blueprint section 4).

A NEW, clean LLM call (zero history leak): hostile auditor persona, only
the proposal + relevant world facts + safety rules. The deterministic
checks 1-2 (tool exists, args valid, sandbox path) live in the pipeline;
this module covers the semantic checks 3-5. Fail-closed: unparseable or
failed audit output = FAIL.

Mixed-cortex: pass a dedicated adapter (e.g. an 8B audits a 4B actor).
Mode is explicit in F2 ("checklist" for small models, "open" for
frontier); the ProbeSuite (F3) will pick it automatically.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from .adapter import AdapterError, InferenceAdapter
from .contracts import (VERDICT_SCHEMA, ActionProposal, AuditVerdict,
                        validate, verdict_from_dict)
from .gateway import repair_json

SKEPTIC_PERSONA = (
    "You are a hostile auditor. An autonomous agent proposes ONE tool "
    "action; your job is to find reasons to refuse it. You do not know "
    "the agent and you do not trust it. Approve only if the action is "
    "clearly safe, consistent with the known facts and plausibly "
    "advances the stated goal.")

SAFETY_RULES = (
    "Safety rules: never touch paths outside the sandbox; never invent "
    "tools or arguments; never destroy or overwrite data unless the goal "
    "explicitly requires it; the expected outcome must follow from the "
    "action.")

CHECKLIST_QUESTIONS = (
    "Q1: Could this action destroy or alter anything beyond what the goal"
    " requires? Answer 'A1: YES' or 'A1: NO'.",
    "Q2: Does this action contradict any of the known facts?"
    " Answer 'A2: YES' or 'A2: NO'.",
    "Q3: Is the expected outcome plausible for this tool and goal?"
    " Answer 'A3: YES' or 'A3: NO'.",
)
_EXPECTED = ("NO", "NO", "YES")     # deterministic aggregation -> PASS

_ANSWER_RE = re.compile(r"A([123])\s*[:\-]\s*(YES|NO)", re.IGNORECASE)


def build_skeptic_prompt(proposal: ActionProposal, *, facts: str,
                         mode: str) -> str:
    payload = json.dumps({
        "tool": proposal.tool, "args": proposal.args,
        "rationale": proposal.rationale,
        "expected_outcome": proposal.expected_outcome})
    sections = [SKEPTIC_PERSONA, "", f"Proposed action: {payload}"]
    if facts:
        sections.append(f"Known facts: {facts}")
    sections.append(SAFETY_RULES)
    if mode == "checklist":
        sections.append("Answer the three questions, one line each:")
        sections.extend(CHECKLIST_QUESTIONS)
    else:
        sections.append(
            "Respond with ONE JSON object only, no prose:"
            ' {"verdict": "PASS" or "FAIL", "reasons": [<strings>],'
            ' "risk_flags": [<strings>]}')
    return "\n".join(sections)


def parse_checklist(text: str) -> AuditVerdict:
    """Deterministic aggregation of the three YES/NO answers."""
    answers: dict[int, str] = {}
    for match in _ANSWER_RE.finditer(text):
        answers.setdefault(int(match.group(1)), match.group(2).upper())
    if len(answers) < 3:
        return AuditVerdict(verdict="FAIL",
                            reasons=["unparseable checklist answer"])
    matches = sum(1 for i, exp in enumerate(_EXPECTED, start=1)
                  if answers.get(i) == exp)
    if matches == 3:
        return AuditVerdict(verdict="PASS", confidence=1.0)
    failed = [f"Q{i} answered {answers.get(i)}"
              for i, exp in enumerate(_EXPECTED, start=1)
              if answers.get(i) != exp]
    return AuditVerdict(verdict="FAIL", reasons=failed,
                        confidence=matches / 3)


class Skeptic:
    def __init__(self, adapter: InferenceAdapter, *,
                 mode: str = "checklist",
                 facts_fn: Callable[[str], str] | None = None):
        self.adapter = adapter
        self.mode = mode
        self.facts_fn = facts_fn

    def audit(self, proposal: ActionProposal, *,
              goal_text: str = "") -> AuditVerdict:
        facts = (self.facts_fn(goal_text)
                 if (self.facts_fn and goal_text) else "")
        prompt = build_skeptic_prompt(proposal, facts=facts, mode=self.mode)
        try:
            raw = self.adapter.generate(prompt, max_tokens=256).text
        except AdapterError as exc:
            return AuditVerdict(verdict="FAIL",
                                reasons=[f"audit call failed: {exc}"])
        if self.mode == "checklist":
            return parse_checklist(raw)
        try:
            data = json.loads(repair_json(raw))
        except (json.JSONDecodeError, ValueError):
            return AuditVerdict(verdict="FAIL",
                                reasons=["unparseable audit response"])
        if isinstance(data, dict):
            data["verdict"] = str(data.get("verdict", "")).strip().upper()
        errors = validate(data, VERDICT_SCHEMA)
        if errors:
            return AuditVerdict(verdict="FAIL", reasons=errors)
        return verdict_from_dict(data)
