import sqlite3

from conscio.agency.ledger import ActionLedger
from conscio.noosphere import audit, record_publish
from conscio.noosphere.paths import conscio_db_path


def _seed(storage, *, rows):
    """rows: list of (goal_fp, status, ok, verdict)."""
    db = conscio_db_path(storage)
    db.parent.mkdir(parents=True, exist_ok=True)      # engine normally creates it
    led = ActionLedger(db)
    for goal_fp, status, ok, verdict in rows:
        rid = led.record(goal_fp=goal_fp, tool="write", args_json="{}",
                         rationale="", tier="low", status=status,
                         ok=(None if ok is None else bool(ok)))
        if verdict:
            led.update_verdict(rid, verdict, [])
    led.close()


def test_two_instance_audit_good_vs_bad(tmp_path):
    noo = tmp_path / "noosphere.db"
    a_good = tmp_path / "A"
    a_bad = tmp_path / "Abad"
    b = tmp_path / "B"
    _seed(a_good, rows=[(f"g{i}", "executed", 1, "PASS") for i in range(12)])
    _seed(a_bad, rows=[(f"g{i}", "executed", 1, "PASS") for i in range(12)]
          + [("gx", "executed", 1, "FAIL")])         # executed-after-FAIL → RED
    record_publish.run(storage=a_good, noosphere=noo)
    record_publish.run(storage=a_bad, noosphere=noo)

    rep = audit.run(storage=b, noosphere=noo)
    verdicts = sorted(p.verdict for p in rep.peers)
    assert verdicts == ["REJECTED", "TRUSTED"]
    assert any(p.executed_after_fail == 1 and p.verdict == "REJECTED"
               for p in rep.peers)


def test_tampered_bundle_is_rejected_not_audited(tmp_path):
    noo = tmp_path / "noosphere.db"
    a = tmp_path / "A"
    _seed(a, rows=[(f"g{i}", "executed", 1, "PASS") for i in range(12)])
    record_publish.run(storage=a, noosphere=noo)
    conn = sqlite3.connect(str(noo))                  # corrupt the stored bytes
    conn.execute("UPDATE published_records SET bundle_json = bundle_json || X'20'")
    conn.commit()
    conn.close()
    rep = audit.run(storage=tmp_path / "B", noosphere=noo)
    assert rep.peers == ()
    assert rep.rejected_bundles and "tampered" in rep.rejected_bundles[0][2]


def test_auditing_self_yields_no_peers(tmp_path):
    noo = tmp_path / "noosphere.db"
    a = tmp_path / "A"
    _seed(a, rows=[(f"g{i}", "executed", 1, "PASS") for i in range(12)])
    record_publish.run(storage=a, noosphere=noo)
    rep = audit.run(storage=a, noosphere=noo)         # same instance audits itself
    assert rep.peers == () and rep.audited == 0
