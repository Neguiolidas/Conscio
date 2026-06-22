import json

from conscio.noosphere import artifact, audit, record, record_catalog


def _entry(seq, status, ok, verdict="", goal_fp="g", tool="write"):
    return record.RecordEntry(seq=seq, ts=float(seq), goal_fp=goal_fp, tool=tool,
                              tier="low", status=status, ok=ok, verdict=verdict)


def _bundle_row(entries, iid="A"):
    body = record.build_bundle_body(entries)
    canon = artifact.canonical_bytes(body)
    return record_catalog.RecordRow(
        origin_instance_id=iid, origin_label="lab", published_ts=1.0,
        content_sha256=artifact.content_hash(canon), entry_count=len(entries),
        window_first_ts=1.0, window_last_ts=float(len(entries)),
        bundle_json=canon, schema_version=1)


def test_foreign_trust_level_boundaries():
    assert audit.foreign_trust_level(0.69, 10, False) == 1
    assert audit.foreign_trust_level(0.70, 9, False) == 1     # too few attempts
    assert audit.foreign_trust_level(0.70, 10, False) == 2
    assert audit.foreign_trust_level(0.85, 10, False) == 3
    assert audit.foreign_trust_level(0.85, 10, True) == 2     # a quarantine caps L2


def test_tool_stats_accuracy():
    entries = [_entry(1, "executed", 1), _entry(2, "executed", 1),
               _entry(3, "failed", 0), _entry(4, "proposed", None)]
    stats = audit.tool_stats(entries)
    s = stats["write"]
    assert (s.ok, s.failed, s.attempts) == (2, 1, 3)
    assert abs(s.accuracy - 2 / 3) < 1e-9                     # proposed excluded


def test_derive_quarantines_streak():
    g = [_entry(1, "failed", 0), _entry(2, "failed", 0), _entry(3, "failed", 0)]
    assert audit.derive_quarantines(g) == {"g"}              # streak 3 >= 3
    g2 = [_entry(1, "failed", 0), _entry(2, "executed", 1), _entry(3, "failed", 0)]
    assert audit.derive_quarantines(g2) == set()            # broken streak


def test_discipline_flags_red_yellow_pass_neutral():
    entries = [_entry(1, "executed", 1, "FAIL"),            # RED
               _entry(2, "executed", 1, ""),               # YELLOW
               _entry(3, "executed", 1, "PASS"),           # neutral
               _entry(4, "rejected", None, "FAIL")]        # not executed → ignore
    red, yellow = audit.discipline_flags(entries)
    assert (red, yellow) == (1, 1)


def test_revalidate_tampered_corrupt_malformed_ok():
    good = _bundle_row([_entry(1, "executed", 1, "PASS")])
    assert audit.revalidate_bundle(good).result == "ok"
    tampered = record_catalog.RecordRow(**{**good.__dict__,
                                           "content_sha256": "deadbeef"})
    assert audit.revalidate_bundle(tampered).result == "tampered"
    corrupt = record_catalog.RecordRow(**{**good.__dict__, "bundle_json": b"\xff\xfe",
        "content_sha256": artifact.content_hash(b"\xff\xfe")})
    assert audit.revalidate_bundle(corrupt).result == "corrupt"
    badbody = json.dumps({"schema_version": 1, "entries": [{"seq": 1}]}).encode()
    malformed = record_catalog.RecordRow(**{**good.__dict__, "bundle_json": badbody,
        "content_sha256": artifact.content_hash(badbody)})
    assert audit.revalidate_bundle(malformed).result == "malformed"


def test_audit_peer_verdicts():
    # TRUSTED: 12 clean executed/PASS on one tool
    good = [_entry(i, "executed", 1, "PASS", goal_fp=f"g{i}") for i in range(12)]
    assert audit.audit_peer(_bundle_row(good), good).verdict == "TRUSTED"
    # REJECTED: one executed-after-FAIL
    bad = good + [_entry(99, "executed", 1, "FAIL", goal_fp="gx")]
    pa = audit.audit_peer(_bundle_row(bad), bad)
    assert pa.verdict == "REJECTED" and pa.executed_after_fail == 1
    # SUSPECT: a quarantined goal, no RED
    susp = good + [_entry(50, "failed", 0, goal_fp="q"),
                   _entry(51, "failed", 0, goal_fp="q"),
                   _entry(52, "failed", 0, goal_fp="q")]
    assert audit.audit_peer(_bundle_row(susp), susp).verdict == "SUSPECT"
    # boundary: unaudited fraction EXACTLY 0.5 → not SUSPECT (rule is strict >)
    half = ([_entry(i, "executed", 1, "", goal_fp=f"h{i}") for i in range(6)]
            + [_entry(6 + i, "executed", 1, "PASS", goal_fp=f"p{i}")
               for i in range(6)])
    assert audit.audit_peer(_bundle_row(half), half).verdict == "TRUSTED"
    # over half unaudited (7/13 > 0.5) → SUSPECT (acc still 1.0, L3, no RED/quarantine)
    over = half + [_entry(99, "executed", 1, "", goal_fp="h99")]
    assert audit.audit_peer(_bundle_row(over), over).verdict == "SUSPECT"
    # INSUFFICIENT: no terminal actions
    none = [_entry(1, "proposed", None)]
    assert audit.audit_peer(_bundle_row(none), none).verdict == "INSUFFICIENT"


def test_run_latest_per_peer_skips_self_and_flags_tampered(tmp_path):
    noo = tmp_path / "noosphere.db"
    good = [_entry(i, "executed", 1, "PASS", goal_fp=f"g{i}") for i in range(12)]
    # peer B: clean → TRUSTED, two snapshots (latest wins)
    record_catalog.publish_rows(noo, [_bundle_row(good, iid="B")])
    good2 = good + [_entry(20, "executed", 1, "PASS", goal_fp="g20")]
    rowB2 = _bundle_row(good2, iid="B")
    object.__setattr__(rowB2, "published_ts", 5.0)
    record_catalog.publish_rows(noo, [rowB2])
    # peer C: tampered bundle
    rowC = _bundle_row(good, iid="C")
    object.__setattr__(rowC, "content_sha256", "deadbeef")
    record_catalog.publish_rows(noo, [rowC])

    # auditor is instance "A" (writes nothing), seeded by load_or_create
    storage = tmp_path / "A"
    rep = audit.run(storage=storage, noosphere=noo)
    verdicts = {p.origin_instance_id: p for p in rep.peers}
    assert verdicts["B"].verdict == "TRUSTED"
    assert verdicts["B"].entry_count == 13                  # latest snapshot
    assert ("C", "lab") == (rep.rejected_bundles[0][0], rep.rejected_bundles[0][1])
    assert "A" not in verdicts                              # never audits self


def test_run_no_records(tmp_path):
    rep = audit.run(storage=tmp_path / "A", noosphere=tmp_path / "none.db")
    assert rep.peers == () and rep.audited == 0
