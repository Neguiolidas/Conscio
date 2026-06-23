# conscio/liaison/review.py
"""Pure cross-agent review protocol over the mailbox (v2.6.0).

fingerprint() is the cross-agent key binding a verdict to one exact proposal:
it includes the proposer instance_id (no cross-proposer collision) and the local
ledger_id (no intra-proposer alias), hashed over canonical (sorted-key) JSON via
the shared noosphere content-hash primitive. build_*/parse_* shape and validate
the message payloads. Never imports conscio.engine."""
from __future__ import annotations

from dataclasses import dataclass

from ..noosphere.artifact import canonical_bytes, content_hash

DECISIONS = {"approve", "reject"}


def fingerprint(proposer_id: str, goal_fp: str, tool: str, args: object,
                ledger_id: int) -> str:
    body = {"v": 1, "proposer": proposer_id, "goal_fp": goal_fp,
            "tool": tool, "args": args, "ledger_id": ledger_id}
    return content_hash(canonical_bytes(body))


@dataclass(frozen=True)
class ReviewRequest:
    fp: str
    tool: str
    args: dict
    goal: str
    verdict: str
    rationale: str


@dataclass(frozen=True)
class ReviewVerdict:
    fp: str
    decision: str
    reason: str


def build_request(*, fp: str, tool: str, args: dict, goal: str, verdict: str,
                  rationale: str) -> dict:
    return {"fp": fp, "tool": tool, "args": args, "goal": goal,
            "verdict": verdict, "rationale": rationale}


def build_verdict(*, fp: str, decision: str, reason: str) -> dict:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
    return {"fp": fp, "decision": decision, "reason": reason}


def parse_request(payload: object) -> ReviewRequest:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    fp = payload.get("fp")
    if not isinstance(fp, str) or not fp:
        raise ValueError("request.fp must be a non-empty string")
    args = payload.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("request.args must be an object")
    return ReviewRequest(
        fp=fp, tool=str(payload.get("tool", "")), args=args,
        goal=str(payload.get("goal", "")),
        verdict=str(payload.get("verdict", "")),
        rationale=str(payload.get("rationale", "")))


def parse_verdict(payload: object) -> ReviewVerdict:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    fp = payload.get("fp")
    if not isinstance(fp, str) or not fp:
        raise ValueError("verdict.fp must be a non-empty string")
    decision = payload.get("decision")
    if decision not in DECISIONS:
        raise ValueError(f"verdict.decision must be one of {sorted(DECISIONS)}")
    return ReviewVerdict(fp=fp, decision=decision,
                         reason=str(payload.get("reason", "")))
