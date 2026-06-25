# conscio/agency/review_apply.py
"""Apply inbound review verdicts to local pending acts — the shared core behind
the proposer-side `poll_reviews` tool AND the awake `--auto-review` auto-apply
(v2.6.2). An agency→liaison bridge: `conscio/liaison/*.py` stays engine-free.
The `host_act` gate is the authority; a peer verdict is only input."""
from __future__ import annotations

import json

from ..liaison import mailbox, review


def _row_args(row: dict) -> dict:
    try:
        a = json.loads(row.get("args_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return a if isinstance(a, dict) else {}


def apply_verdicts(host_act, liaison_db, self_id: str, reviewers, *,
                   limit: int = 50) -> list[dict]:
    """Apply allowlisted verdicts to pending acts; return applied packets.
    No-op (``[]``) when host_act is None or liaison_db/self_id is empty."""
    if host_act is None or not liaison_db or not self_id:
        return []
    reviewers = set(reviewers)
    verdicts = mailbox.inbox(liaison_db, self_id, types=["review_verdict"],
                             unread_only=True, limit=limit)
    pend = host_act.pending(200)
    fp_to_id = {review.fingerprint(self_id, r["goal_fp"], r["tool"],
                                   _row_args(r), r["id"]): r["id"] for r in pend}
    applied: list[dict] = []
    read_ids: list[int] = []
    for m in verdicts:
        read_ids.append(m["id"])                  # bound work: mark all polled
        if m["from_instance"] not in reviewers:
            continue
        try:
            v = review.parse_verdict(m["payload"])
        except ValueError:
            continue
        lid = fp_to_id.get(v.fp)
        if lid is None:
            continue
        res = (host_act.approve(lid) if v.decision == "approve"
               else host_act.reject(lid, v.reason))
        applied.append({"ledger_id": lid, "decision": v.decision,
                        "status": res.get("status"), "packet": res.get("packet")})
    if read_ids:
        mailbox.mark_read(liaison_db, read_ids)
    return applied
