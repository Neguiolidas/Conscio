# conscio/noosphere/audit.py
"""Mutual audit — B forms its OWN trust opinion about a peer A from A's
published behavioral bundle, using B's deterministic kernels + thresholds.
Read-only, no inherited trust, report-only. Engine-free; no LLM/network.

The LLM Skeptic replay is deferred (v2.2.2/v2.3); here 'Skeptic' is the
deterministic discipline check: did A execute actions its own Skeptic FAILed?"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from . import artifact, record, record_catalog
from .identity import load_or_create
from .paths import resolve_noosphere, resolve_storage

# ── policy constants (parity-tested vs the engine; see test_noosphere_parity) ──
BREAKER_THRESHOLD = 3       # == conscio.agency.breaker.DEFAULT_MAX_RETRIES
L2_ACCURACY = 0.7           # == conscio.agency.trust.L2_ACCURACY
L3_ACCURACY = 0.85          # == conscio.agency.trust.L3_ACCURACY
AUTONOMY_MIN_ROWS = 10      # == conscio.agency.trust.AUTONOMY_MIN_ROWS
# ── audit-only policy (no engine equivalent) ──
ACCURACY_FLOOR = 0.5              # below this (with volume) → REJECTED
UNAUDITED_FRACTION_SUSPECT = 0.5  # executed_unaudited / executed above this → SUSPECT

_TERMINAL = frozenset({"executed", "failed"})


@dataclass(frozen=True)
class RevalidationOutcome:
    result: str            # 'ok'|'tampered'|'corrupt'|'malformed'
    error: str = ""
    body: dict | None = None

    @property
    def ok(self) -> bool:
        return self.result == "ok"


@dataclass(frozen=True)
class ToolStats:
    tool: str
    ok: int
    failed: int
    attempts: int
    accuracy: float
    trust_level: int


@dataclass(frozen=True)
class PeerAudit:
    origin_instance_id: str
    origin_label: str
    published_ts: float
    entry_count: int
    attempts: int
    overall_accuracy: float
    tools: tuple[ToolStats, ...]
    quarantined_goals: tuple[str, ...]
    executed_after_fail: int
    executed_unaudited: int
    verdict: str


def revalidate_bundle(row: record_catalog.RecordRow) -> RevalidationOutcome:
    if artifact.content_hash(row.bundle_json) != row.content_sha256:
        return RevalidationOutcome("tampered", "content_sha256 mismatch")
    try:
        body = json.loads(row.bundle_json.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return RevalidationOutcome("corrupt", f"unparseable bundle: {exc}")
    if not record.well_typed_bundle(body):
        return RevalidationOutcome("malformed", "missing or mis-typed fields",
                                   body=body if isinstance(body, dict) else None)
    if body.get("schema_version") != record.BUNDLE_SCHEMA:
        return RevalidationOutcome(
            "malformed",
            f"unsupported schema_version {body.get('schema_version')!r}", body=body)
    return RevalidationOutcome("ok", body=body)


def tool_stats(entries: list[record.RecordEntry]) -> dict[str, ToolStats]:
    agg: dict[str, list[int]] = {}            # tool -> [ok, failed]
    for e in entries:
        if e.status not in _TERMINAL:
            continue
        slot = agg.setdefault(e.tool, [0, 0])
        if e.status == "executed" and e.ok == 1:
            slot[0] += 1
        else:
            slot[1] += 1
    out: dict[str, ToolStats] = {}
    for tool, (ok, failed) in agg.items():
        attempts = ok + failed
        acc = ok / attempts if attempts else 0.0
        out[tool] = ToolStats(tool=tool, ok=ok, failed=failed,
                              attempts=attempts, accuracy=acc, trust_level=1)
    return out


def _max_fail_streak(statuses: list[str]) -> int:
    best = cur = 0
    for s in statuses:
        if s == "failed":
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def derive_quarantines(entries: list[record.RecordEntry]) -> set[str]:
    by_goal: dict[str, list[tuple[int, str]]] = {}
    for e in entries:
        by_goal.setdefault(e.goal_fp, []).append((e.seq, e.status))
    out: set[str] = set()
    for goal_fp, seq_status in by_goal.items():
        seq_status.sort()
        if _max_fail_streak([s for _, s in seq_status]) >= BREAKER_THRESHOLD:
            out.add(goal_fp)
    return out


def foreign_trust_level(accuracy: float, attempts: int,
                        any_quarantine: bool) -> int:
    if (accuracy >= L3_ACCURACY and attempts >= AUTONOMY_MIN_ROWS
            and not any_quarantine):
        return 3
    if accuracy >= L2_ACCURACY and attempts >= AUTONOMY_MIN_ROWS:
        return 2
    return 1


def discipline_flags(entries: list[record.RecordEntry]) -> tuple[int, int]:
    """(executed_after_fail RED, executed_unaudited YELLOW). PASS is neutral."""
    red = yellow = 0
    for e in entries:
        if e.status != "executed":
            continue
        v = e.verdict.strip().upper()
        if v == "FAIL":
            red += 1
        elif v == "":
            yellow += 1
    return red, yellow


def _verdict(attempts: int, overall: float, quarantines: set[str], red: int,
             yellow: int, executed: int, tools: list[ToolStats]) -> str:
    if attempts == 0:
        return "INSUFFICIENT"
    if red > 0 or (overall < ACCURACY_FLOOR and attempts >= AUTONOMY_MIN_ROWS):
        return "REJECTED"
    unaudited_frac = (yellow / executed) if executed else 0.0
    no_l2 = not any(t.trust_level >= 2 for t in tools)
    if quarantines or unaudited_frac > UNAUDITED_FRACTION_SUSPECT or no_l2:
        return "SUSPECT"
    return "TRUSTED"


def audit_peer(row: record_catalog.RecordRow,
               entries: list[record.RecordEntry]) -> PeerAudit:
    quarantines = derive_quarantines(entries)
    any_q = bool(quarantines)
    tools: list[ToolStats] = []
    total_ok = total_attempts = 0
    for ts in tool_stats(entries).values():
        level = foreign_trust_level(ts.accuracy, ts.attempts, any_q)
        tools.append(ToolStats(tool=ts.tool, ok=ts.ok, failed=ts.failed,
                               attempts=ts.attempts, accuracy=ts.accuracy,
                               trust_level=level))
        total_ok += ts.ok
        total_attempts += ts.attempts
    red, yellow = discipline_flags(entries)
    executed = sum(1 for e in entries if e.status == "executed")
    overall = total_ok / total_attempts if total_attempts else 0.0
    verdict = _verdict(total_attempts, overall, quarantines, red, yellow,
                       executed, tools)
    tools.sort(key=lambda t: t.tool)
    return PeerAudit(
        origin_instance_id=row.origin_instance_id, origin_label=row.origin_label,
        published_ts=row.published_ts, entry_count=row.entry_count,
        attempts=total_attempts, overall_accuracy=overall, tools=tuple(tools),
        quarantined_goals=tuple(sorted(quarantines)), executed_after_fail=red,
        executed_unaudited=yellow, verdict=verdict)


@dataclass(frozen=True)
class AuditReport:
    peers: tuple[PeerAudit, ...]
    rejected_bundles: tuple[tuple[str, str, str], ...]   # (origin_id, label, reason)
    audited: int


def run(storage: str | os.PathLike[str] | None = None,
        noosphere: str | os.PathLike[str] | None = None,
        instance: str | None = None) -> AuditReport:
    storage = resolve_storage(storage)
    noo = resolve_noosphere(noosphere)
    ident = load_or_create(storage)
    foreign = record_catalog.read_foreign(noo, exclude_instance_id=ident.instance_id)

    latest: dict[str, record_catalog.RecordRow] = {}     # keep newest per origin
    for r in foreign:
        if instance and r.origin_instance_id != instance:
            continue
        cur = latest.get(r.origin_instance_id)
        if cur is None or r.published_ts > cur.published_ts:
            latest[r.origin_instance_id] = r

    peers: list[PeerAudit] = []
    rejected: list[tuple[str, str, str]] = []
    for r in sorted(latest.values(),
                    key=lambda x: (x.published_ts, x.origin_instance_id)):
        outcome = revalidate_bundle(r)
        if not outcome.ok or outcome.body is None:
            rejected.append((r.origin_instance_id, r.origin_label,
                             f"{outcome.result}: {outcome.error}".strip(": ")))
            continue
        peers.append(audit_peer(r, record.entries_from_body(outcome.body)))
    return AuditReport(peers=tuple(peers), rejected_bundles=tuple(rejected),
                       audited=len(peers))
