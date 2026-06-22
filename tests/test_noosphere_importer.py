# tests/test_noosphere_importer.py
import json
from conscio.noosphere import importer, catalog, artifact, quarantine


def _catalog_row(*, iid="A", goal_text="deploy", tamper=False, fp=None,
                 tools=("a",)):
    plan = [{"tool": t, "args": {}, "rationale": "r"} for t in tools]
    from conscio.agency.fingerprint import goal_fingerprint
    body = artifact.build_body(
        goal_fp=fp if fp is not None else goal_fingerprint(goal_text),
        goal_text=goal_text, tool_seq=list(tools), plan_template=plan)
    canon = artifact.canonical_bytes(body)
    sha = artifact.content_hash(canon)
    blob = canon if not tamper else canon + b" "
    return catalog.CatalogRow(
        origin_instance_id=iid, origin_label=f"{iid}-box", goal_fp=body["goal_fp"],
        goal_text=goal_text, tool_seq=json.dumps(list(tools)),
        plan_template=json.dumps(plan), published_ts=1.0,
        content_sha256=sha, artifact_json=blob, schema_version=1)


def test_valid_import_is_ok():
    out = importer.revalidate(_catalog_row())
    assert out.result == "ok" and out.body is not None


def test_tampered_blob_rejected():
    out = importer.revalidate(_catalog_row(tamper=True))
    assert out.result == "tampered" and not out.ok


def test_fp_mismatch_rejected():
    assert importer.revalidate(
        _catalog_row(fp="0000000000000000")).result == "fp_mismatch"


def test_inconsistent_tool_seq_rejected():
    from conscio.agency.fingerprint import goal_fingerprint
    body = artifact.build_body(
        goal_fp=goal_fingerprint("deploy"), goal_text="deploy",
        tool_seq=["b"], plan_template=[{"tool": "a", "args": {}, "rationale": "r"}])
    canon = artifact.canonical_bytes(body)
    row = catalog.CatalogRow(
        origin_instance_id="A", origin_label="A", goal_fp=body["goal_fp"],
        goal_text="deploy", tool_seq='["b"]',
        plan_template='[{"tool":"a","args":{},"rationale":"r"}]',
        published_ts=1.0, content_sha256=artifact.content_hash(canon),
        artifact_json=canon, schema_version=1)
    assert importer.revalidate(row).result == "malformed"


def test_unsupported_schema_version_rejected():
    from conscio.agency.fingerprint import goal_fingerprint
    body = {"schema_version": 999, "goal_fp": goal_fingerprint("deploy"),
            "goal_text": "deploy", "tool_seq": ["a"],
            "plan_template": [{"tool": "a", "args": {}, "rationale": "r"}]}
    canon = artifact.canonical_bytes(body)
    row = catalog.CatalogRow(
        origin_instance_id="A", origin_label="A", goal_fp=body["goal_fp"],
        goal_text="deploy", tool_seq='["a"]',
        plan_template='[{"tool":"a","args":{},"rationale":"r"}]',
        published_ts=1.0, content_sha256=artifact.content_hash(canon),
        artifact_json=canon, schema_version=999)
    assert importer.revalidate(row).result == "malformed"


def test_corrupt_blob_rejected():
    blob = b"\xff\xfenot json"
    row = catalog.CatalogRow(
        origin_instance_id="A", origin_label="A", goal_fp="fp", goal_text="x",
        tool_seq="[]", plan_template="[]", published_ts=1.0,
        content_sha256=artifact.content_hash(blob), artifact_json=blob,
        schema_version=1)
    assert importer.revalidate(row).result == "corrupt"


def test_run_quarantines_foreign_and_skips_self(tmp_path):
    noo = tmp_path / "noosphere.db"
    catalog.publish_rows(noo, [_catalog_row(iid="A", goal_text="deploy"),
                               _catalog_row(iid="A", goal_text="rollback")])
    res = importer.run(storage=tmp_path, noosphere=noo)
    assert res.quarantined == 2 and res.rejected == 0
    rows = quarantine.list_rows(tmp_path / "noosphere_quarantine.db")
    assert all(r.import_status == "quarantined" for r in rows)
    # second import is idempotent
    assert importer.run(storage=tmp_path, noosphere=noo).skipped == 2
