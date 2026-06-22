# conscio/noosphere/record.py
"""Behavioral bundle artifact — a minimal, non-sensitive projection of an
instance's action ledger. The content body is hashed via
artifact.canonical_bytes / content_hash (same convention as the skill
artifact). Carries ONLY {seq, ts, goal_fp, tool, tier, status, ok, verdict};
never args/output/error/rationale/goal_text (privacy by column omission)."""
from __future__ import annotations

from dataclasses import dataclass

BUNDLE_SCHEMA = 1

_VALID_STATUS = frozenset(
    {"proposed", "executing", "executed", "rejected", "failed", "locked"})


@dataclass(frozen=True)
class RecordEntry:
    seq: int
    ts: float
    goal_fp: str
    tool: str
    tier: str
    status: str
    ok: int | None
    verdict: str


def build_bundle_body(entries: list[RecordEntry]) -> dict:
    return {
        "schema_version": BUNDLE_SCHEMA,
        "entries": [
            {"seq": e.seq, "ts": e.ts, "goal_fp": e.goal_fp, "tool": e.tool,
             "tier": e.tier, "status": e.status, "ok": e.ok,
             "verdict": e.verdict}
            for e in entries],
    }


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _well_typed_entry(d: object) -> bool:
    if not isinstance(d, dict):
        return False
    if not _is_int(d.get("seq")):
        return False
    ts = d.get("ts")
    if not ((isinstance(ts, (int, float)) and not isinstance(ts, bool))):
        return False
    if not (isinstance(d.get("goal_fp"), str) and d["goal_fp"]):
        return False
    if not (isinstance(d.get("tool"), str) and isinstance(d.get("tier"), str)
            and isinstance(d.get("verdict"), str)):
        return False
    if d.get("status") not in _VALID_STATUS:
        return False
    ok = d.get("ok")
    if ok is not None and not (ok in (0, 1) and not isinstance(ok, bool)):
        return False
    return True


def well_typed_bundle(body: object) -> bool:
    if not isinstance(body, dict) or not isinstance(body.get("entries"), list):
        return False
    return all(_well_typed_entry(e) for e in body["entries"])


def entries_from_body(body: dict) -> list[RecordEntry]:
    out: list[RecordEntry] = []
    for d in body["entries"]:
        out.append(RecordEntry(
            seq=int(d["seq"]), ts=float(d["ts"]), goal_fp=str(d["goal_fp"]),
            tool=str(d["tool"]), tier=str(d["tier"]), status=str(d["status"]),
            ok=(None if d["ok"] is None else int(d["ok"])),
            verdict=str(d["verdict"])))
    return out
