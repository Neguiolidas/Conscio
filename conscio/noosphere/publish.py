# conscio/noosphere/publish.py
"""Publish locally-proven skills to the shared catalog.

Opens conscio.db READ-ONLY (file:...?mode=ro) — no PRAGMA at all, SELECT only.
Engine-free: imports artifact/catalog/identity/paths + the fingerprint leaf and
the stdlib; never SkillLibrary/ToolRegistry/engine. MIN_SERVE_RATE is duplicated
here (a parity test asserts it equals conscio.agency.skills.MIN_SERVE_RATE)."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from conscio.agency.fingerprint import goal_fingerprint

from . import artifact, catalog
from .identity import load_or_create
from .paths import conscio_db_path, resolve_noosphere, resolve_storage

MIN_SERVE_RATE = 0.5     # MUST equal conscio.agency.skills.MIN_SERVE_RATE


@dataclass(frozen=True)
class PublishResult:
    published: int
    skipped: int
    considered: int
    malformed: int = 0


def _open_conscio_ro(path: str | os.PathLike[str]) -> sqlite3.Connection:
    # read-only; issue NO pragma at all (not even busy_timeout); SELECT only.
    # as_uri() percent-encodes spaces/?/#/% etc. and requires an absolute path.
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rate(successes: int, failures: int) -> float:
    total = successes + failures
    return successes / total if total else 0.0


def run(storage: str | os.PathLike[str] | None = None,
        noosphere: str | os.PathLike[str] | None = None) -> PublishResult:
    storage = resolve_storage(storage)
    noo = resolve_noosphere(noosphere)
    ident = load_or_create(storage)
    db = conscio_db_path(storage)
    if not db.exists():
        return PublishResult(0, 0, 0)
    conn = _open_conscio_ro(db)
    try:
        try:
            skills = conn.execute(
                "SELECT goal_fp, goal_text, tool_seq, plan_template,"
                " successes, failures FROM skills").fetchall()
        except sqlite3.OperationalError:
            return PublishResult(0, 0, 0)        # no skills table yet
    finally:
        conn.close()

    rows: list[catalog.CatalogRow] = []
    malformed = 0
    now = time.time()
    for s in skills:
        if _rate(s["successes"], s["failures"]) < MIN_SERVE_RATE:
            continue
        # A corrupt/malformed LOCAL row must never crash publish or pollute the
        # catalog — skip it and count it. We also self-verify goal_fp so we never
        # publish an artifact whose own fingerprint is wrong.
        try:
            tool_seq = json.loads(s["tool_seq"])
            plan_template = json.loads(s["plan_template"])
            if not (isinstance(tool_seq, list)
                    and all(isinstance(t, str) for t in tool_seq)
                    and isinstance(plan_template, list)):
                raise ValueError("tool_seq/plan_template wrong shape")
            if goal_fingerprint(s["goal_text"]) != s["goal_fp"]:
                raise ValueError("goal_fp != goal_fingerprint(goal_text)")
        except (ValueError, TypeError):
            malformed += 1
            continue
        body = artifact.build_body(
            goal_fp=s["goal_fp"], goal_text=s["goal_text"],
            tool_seq=tool_seq, plan_template=plan_template)
        canon = artifact.canonical_bytes(body)
        rows.append(catalog.CatalogRow(
            origin_instance_id=ident.instance_id, origin_label=ident.label,
            goal_fp=s["goal_fp"], goal_text=s["goal_text"],
            tool_seq=json.dumps(tool_seq), plan_template=json.dumps(plan_template),
            published_ts=now, content_sha256=artifact.content_hash(canon),
            artifact_json=canon, schema_version=artifact.ARTIFACT_SCHEMA))

    inserted = catalog.publish_rows(noo, rows)
    return PublishResult(published=inserted, skipped=len(rows) - inserted,
                         considered=len(rows), malformed=malformed)
