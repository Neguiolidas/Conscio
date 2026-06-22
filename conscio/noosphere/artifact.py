# conscio/noosphere/artifact.py
"""Skill artifact: content-only body + canonical-JSON content hash.

content_sha256 is the identity of the CONTENT — no provenance, no timestamp,
no local stats. published_ts is deliberately excluded (it is catalog metadata;
including it would make the hash unstable per publish)."""
from __future__ import annotations

import hashlib
import json

ARTIFACT_SCHEMA = 1


def build_body(*, goal_fp: str, goal_text: str, tool_seq: list[str],
               plan_template: list[dict]) -> dict:
    return {
        "schema_version": ARTIFACT_SCHEMA,
        "goal_fp": goal_fp,
        "goal_text": goal_text,
        "tool_seq": tool_seq,
        "plan_template": plan_template,
    }


def canonical_bytes(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def content_hash(canonical: bytes) -> str:
    return hashlib.sha256(canonical).hexdigest()
