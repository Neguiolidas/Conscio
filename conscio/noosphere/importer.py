# conscio/noosphere/importer.py
"""Import foreign catalog skills into the local quarantine with execution-free
static revalidation. Never serves, executes, or promotes. Engine-free: the only
conscio import is the goal_fingerprint leaf."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from conscio.agency.fingerprint import goal_fingerprint

from . import artifact, catalog, quarantine
from .identity import load_or_create
from .paths import quarantine_db_path, resolve_noosphere, resolve_storage


@dataclass(frozen=True)
class RevalidationOutcome:
    result: str          # 'ok'|'tampered'|'corrupt'|'malformed'|'fp_mismatch'
    error: str = ""
    body: dict | None = None     # the parsed artifact when JSON decoded (else None)

    @property
    def ok(self) -> bool:
        return self.result == "ok"


@dataclass(frozen=True)
class ImportResult:
    quarantined: int
    rejected: int
    skipped: int


def _well_typed(body: dict) -> bool:
    gt, gfp = body.get("goal_text"), body.get("goal_fp")
    ts, plan = body.get("tool_seq"), body.get("plan_template")
    if not (isinstance(gfp, str) and isinstance(gt, str)
            and isinstance(ts, list) and all(isinstance(t, str) for t in ts)
            and isinstance(plan, list)):
        return False
    for step in plan:
        if not (isinstance(step, dict) and isinstance(step.get("tool"), str)
                and isinstance(step.get("args"), dict)
                and isinstance(step.get("rationale"), str)):
            return False
    return True


def revalidate(row: catalog.CatalogRow) -> RevalidationOutcome:
    # 1. hash (tamper-evidence over the exact bytes)
    if artifact.content_hash(row.artifact_json) != row.content_sha256:
        return RevalidationOutcome("tampered", "content_sha256 mismatch")
    # 2. shape: corrupt (unparseable) vs malformed (wrong fields/types)
    try:
        body = json.loads(row.artifact_json.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return RevalidationOutcome("corrupt", f"unparseable artifact: {exc}")
    if not isinstance(body, dict) or not _well_typed(body):
        return RevalidationOutcome("malformed", "missing or mis-typed fields",
                                   body=body if isinstance(body, dict) else None)
    # 3. schema_version we understand
    if body.get("schema_version") != artifact.ARTIFACT_SCHEMA:
        return RevalidationOutcome(
            "malformed", f"unsupported schema_version {body.get('schema_version')!r}",
            body=body)
    # 4. fp (recompute locally)
    if goal_fingerprint(body["goal_text"]) != body["goal_fp"]:
        return RevalidationOutcome("fp_mismatch",
                                   "recomputed goal_fp != claimed", body=body)
    # 5. consistency: plan tools == tool_seq
    if [s["tool"] for s in body["plan_template"]] != body["tool_seq"]:
        return RevalidationOutcome("malformed",
                                   "plan_template tools != tool_seq", body=body)
    # 6. R1: pure data (already JSON, never executed)
    return RevalidationOutcome("ok", body=body)


def run(storage: str | os.PathLike[str] | None = None,
        noosphere: str | os.PathLike[str] | None = None) -> ImportResult:
    storage = resolve_storage(storage)
    noo = resolve_noosphere(noosphere)
    ident = load_or_create(storage)
    qdb = quarantine_db_path(storage)
    foreign = catalog.read_foreign(noo, exclude_instance_id=ident.instance_id)

    quarantined = rejected = skipped = 0
    now = time.time()
    for cr in foreign:
        outcome = revalidate(cr)
        status = "quarantined" if outcome.ok else "rejected"
        # Authoritative display columns come from the PARSED artifact body when
        # the BLOB decoded; otherwise (tampered/corrupt) fall back to the
        # catalog's denormalized columns. The artifact_json BLOB is always the
        # source of truth regardless.
        b = outcome.body
        if b is not None:
            goal_fp = str(b.get("goal_fp", cr.goal_fp))
            goal_text = str(b.get("goal_text", cr.goal_text))
            tool_seq = json.dumps(b.get("tool_seq"))
            plan_template = json.dumps(b.get("plan_template"))
        else:
            goal_fp, goal_text = cr.goal_fp, cr.goal_text
            tool_seq, plan_template = cr.tool_seq, cr.plan_template
        inserted = quarantine.insert(qdb, quarantine.QuarantineRow(
            content_sha256=cr.content_sha256, origin_instance_id=cr.origin_instance_id,
            origin_label=cr.origin_label, published_ts=cr.published_ts,
            importer_instance_id=ident.instance_id, imported_ts=now,
            goal_fp=goal_fp, goal_text=goal_text, tool_seq=tool_seq,
            plan_template=plan_template, artifact_json=cr.artifact_json,
            import_status=status, revalidation_result=outcome.result,
            revalidation_error=outcome.error, schema_version=cr.schema_version))
        if not inserted:
            skipped += 1
        elif outcome.ok:
            quarantined += 1
        else:
            rejected += 1
    return ImportResult(quarantined=quarantined, rejected=rejected, skipped=skipped)
