# conscio/noosphere/record_publish.py
"""Publish this instance's behavioral record (the action ledger, projected to
non-sensitive columns) to the shared catalog. Opens conscio.db READ-ONLY
(file:...?mode=ro), no PRAGMA at all, SELECT only. Engine-free."""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import artifact, record, record_catalog
from .identity import load_or_create
from .paths import conscio_db_path, resolve_noosphere, resolve_storage


@dataclass(frozen=True)
class PublishRecordResult:
    published: int
    skipped: int
    considered: int
    entries: int


def _open_conscio_ro(path: str | os.PathLike[str]) -> sqlite3.Connection:
    # read-only; issue NO pragma at all; SELECT only. as_uri() percent-encodes
    # and requires an absolute path.
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def run(storage: str | os.PathLike[str] | None = None,
        noosphere: str | os.PathLike[str] | None = None) -> PublishRecordResult:
    storage = resolve_storage(storage)
    noo = resolve_noosphere(noosphere)
    ident = load_or_create(storage)
    db = conscio_db_path(storage)
    if not db.exists():
        return PublishRecordResult(0, 0, 0, 0)
    conn = _open_conscio_ro(db)
    try:
        try:
            rows = conn.execute(
                "SELECT id, ts, goal_fp, tool, tier, status, ok, verdict"
                " FROM actions ORDER BY id ASC").fetchall()
        except sqlite3.OperationalError:
            return PublishRecordResult(0, 0, 0, 0)     # no actions table
    finally:
        conn.close()
    if not rows:
        return PublishRecordResult(0, 0, 0, 0)

    entries = [record.RecordEntry(
        seq=int(r["id"]), ts=float(r["ts"]), goal_fp=str(r["goal_fp"]),
        tool=str(r["tool"]), tier=str(r["tier"] or ""), status=str(r["status"]),
        ok=(None if r["ok"] is None else int(r["ok"])),
        verdict=str(r["verdict"] or "")) for r in rows]
    body = record.build_bundle_body(entries)
    canon = artifact.canonical_bytes(body)
    sha = artifact.content_hash(canon)
    ts_vals = [e.ts for e in entries]
    row = record_catalog.RecordRow(
        origin_instance_id=ident.instance_id, origin_label=ident.label,
        published_ts=time.time(), content_sha256=sha, entry_count=len(entries),
        window_first_ts=min(ts_vals), window_last_ts=max(ts_vals),
        bundle_json=canon, schema_version=record.BUNDLE_SCHEMA)
    inserted = record_catalog.publish_rows(noo, [row])
    return PublishRecordResult(published=inserted, skipped=1 - inserted,
                               considered=1, entries=len(entries))
