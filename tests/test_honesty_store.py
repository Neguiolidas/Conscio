from conscio.honesty import verdicts as o
from conscio.honesty.store import ClaimStore


def test_record_and_read_back(tmp_path):
    store = ClaimStore(tmp_path / "conscio.db")
    cid = store.record("s1", "commit", "abc1234", o.CONTRADICTED, "")
    rows = store.recent()
    assert [r["id"] for r in rows] == [cid]
    assert rows[0]["outcome"] == o.CONTRADICTED
    assert rows[0]["anchor"] == "abc1234"


def test_claims_never_touch_the_actions_table(tmp_path):
    """A TrustMatrix conta linhas de actions; prosa nao pode entrar la."""
    db = tmp_path / "conscio.db"
    ClaimStore(db).record("s1", "commit", "abc1234", o.CONTRADICTED, "")
    from conscio.agency.ledger import ActionLedger
    assert ActionLedger(db).count() == 0


def test_reopening_is_idempotent(tmp_path):
    db = tmp_path / "conscio.db"
    ClaimStore(db)
    ClaimStore(db)
    ClaimStore(db)


def test_it_shares_the_database_with_the_ledger_without_clobbering_it(tmp_path):
    """claims e actions convivem no mesmo arquivo: o ledger abre depois e o
    schema dele nao pode ser afetado, nem vice-versa."""
    db = tmp_path / "conscio.db"
    from conscio.agency.ledger import ActionLedger
    ledger = ActionLedger(db)
    row = ledger.record(goal_fp="g", tool="bash", args_json="{}",
                        rationale="r", tier="T1", status="executed")
    ClaimStore(db).record("s1", "commit", "abc1234", o.VERIFIED, "obs:1")
    assert ActionLedger(db).get(row)["outcome"] == o.PENDING
