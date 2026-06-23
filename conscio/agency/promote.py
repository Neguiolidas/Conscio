# conscio/agency/promote.py
"""Promotion gate for a quarantined foreign skill (v2.3.0).

A pure mechanic: given a quarantined row's trial counters and tool sequence,
decide whether it has earned graduation into the live SkillLibrary. Imports
nothing from conscio.noosphere or conscio.engine. The caller (the engine) owns
the read of the quarantine row, the graft into the library, and the
promoted-stamp; this module only judges."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MIN_PROMOTE_PASSES = 3   # clean local trials required before graduation


@dataclass(frozen=True)
class PromoteResult:
    skill_id: int
    successes: int
    failures: int


@dataclass(frozen=True)
class PromoteRefusal:
    reason: str


@dataclass(frozen=True)
class PromoteDecision:
    ok: bool
    reason: str            # "" when ok


def evaluate_promotion(*, trial_successes: int, trial_failures: int,
                       tool_seq: str, registry: Any) -> PromoteDecision:
    """Gate a quarantined row for promotion. Order: enough clean passes, zero
    failures, decodable tool_seq, every tool present in the live registry."""
    if trial_successes < MIN_PROMOTE_PASSES:
        return PromoteDecision(
            False,
            f"insufficient trials ({trial_successes}/{MIN_PROMOTE_PASSES})")
    if trial_failures != 0:
        return PromoteDecision(False, f"failed {trial_failures} trial(s)")
    try:
        tools = json.loads(tool_seq)
        if not isinstance(tools, list):
            raise ValueError("tool_seq is not a list")
    except (ValueError, TypeError) as exc:
        return PromoteDecision(False, f"corrupt tool_seq: {exc}")
    for tool in tools:
        if registry.get(str(tool)) is None:
            return PromoteDecision(False, f"unknown tool '{tool}'")
    return PromoteDecision(True, "")
