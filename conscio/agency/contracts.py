# conscio/agency/contracts.py
"""
Output contracts for the agentic pipeline (spec section 5.2).

Zero-dep: dataclasses + a small validator. The pydantic upgrade is an
optional extra in a later phase; the core never requires it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

_TYPES = {"str": str, "int": int, "float": float, "bool": bool,
          "dict": dict, "list": list}


def validate(data: Any, schema: dict[str, dict]) -> list[str]:
    """Return a list of human-readable errors; empty list means valid."""
    if not isinstance(data, dict):
        return [f"payload must be a dict, got {type(data).__name__}"]
    errors: list[str] = []
    for key, rules in schema.items():
        if key not in data:
            if rules.get("required", False):
                errors.append(f"missing required field '{key}'")
            continue
        value = data[key]
        expected = _TYPES.get(rules.get("type", "str"), str)
        # bool is a subclass of int — keep them distinct
        if expected is int and isinstance(value, bool):
            errors.append(f"field '{key}' must be int, got bool")
        elif not isinstance(value, expected):
            errors.append(
                f"field '{key}' must be {rules.get('type')}, "
                f"got {type(value).__name__}")
        elif "enum" in rules and value not in rules["enum"]:
            errors.append(f"field '{key}' must be one of {rules['enum']}")
    return errors


PROPOSAL_SCHEMA: dict[str, dict] = {
    "tool": {"type": "str", "required": True},
    "args": {"type": "dict", "required": True},
    "rationale": {"type": "str", "required": True},
    "expected_outcome": {"type": "str", "required": True},
}


@dataclass
class ActionProposal:
    tool: str
    args: dict[str, Any]
    rationale: str
    expected_outcome: str
    goal_id: str = ""
    action_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class ToolResult:
    ok: bool
    output: str
    error: str = ""
    duration_ms: int = 0


def proposal_from_dict(data: dict, *, goal_id: str = "") -> ActionProposal:
    """Build an ActionProposal from an already-validated dict.

    IDs are never produced by the LLM — the pipeline assigns them here.
    """
    return ActionProposal(
        tool=data["tool"], args=data["args"], rationale=data["rationale"],
        expected_outcome=data["expected_outcome"], goal_id=goal_id)


VERDICT_SCHEMA: dict[str, dict] = {
    "verdict": {"type": "str", "required": True, "enum": ["PASS", "FAIL"]},
    "reasons": {"type": "list"},
    "risk_flags": {"type": "list"},
}


@dataclass
class AuditVerdict:
    verdict: str                                # "PASS" | "FAIL"
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    confidence: float = 0.5     # checklist: agreement ratio; open: model-reported
    audited: bool = True        # False = LOW-risk fast path skipped the LLM

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"


def verdict_from_dict(data: dict) -> AuditVerdict:
    """Build an AuditVerdict from an already-validated dict (fail-safe)."""
    conf = data.get("confidence", 0.5)
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        conf = 0.5
    return AuditVerdict(
        verdict=data["verdict"],
        reasons=[str(r) for r in data.get("reasons", [])],
        risk_flags=[str(f) for f in data.get("risk_flags", [])],
        confidence=max(0.0, min(1.0, float(conf))))
