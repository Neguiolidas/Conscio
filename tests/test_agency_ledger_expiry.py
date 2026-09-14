import time

from conscio.agency import outcome as o
from conscio.agency.ledger import EXPIRY_SWEEP_LIMIT, ActionLedger


def _old_row(ledger, age_days):
    row_id = ledger.record(goal_fp="g", tool="bash", args_json="{}",
                           rationale="r", tier="T1", status="executed")
    ledger._conn.execute("UPDATE actions SET ts=? WHERE id=?",
                         (time.time() - age_days * 86400, row_id))
    ledger._conn.commit()
    return row_id


def test_sweep_materialises_expired_pending(tmp_path):
    ledger = ActionLedger(tmp_path / "conscio.db")
    row_id = _old_row(ledger, o.RETENTION_DAYS + 1)
    assert ledger.expire_stale() == 1
    row = ledger.get(row_id)
    assert row["outcome"] == o.UNSUPPORTED
    assert row["outcome_ts"] > 0


def test_sweep_leaves_fresh_pending_alone(tmp_path):
    ledger = ActionLedger(tmp_path / "conscio.db")
    row_id = _old_row(ledger, 1)
    assert ledger.expire_stale() == 0
    assert ledger.get(row_id)["outcome"] == o.PENDING


def test_sweep_is_bounded(tmp_path):
    ledger = ActionLedger(tmp_path / "conscio.db")
    for _ in range(EXPIRY_SWEEP_LIMIT + 5):
        _old_row(ledger, o.RETENTION_DAYS + 1)
    assert ledger.expire_stale() == EXPIRY_SWEEP_LIMIT


def test_sweep_drains_the_backlog_across_passes(tmp_path):
    """O limite escoa em varios turnos; nao pode PERDER linha."""
    ledger = ActionLedger(tmp_path / "conscio.db")
    for _ in range(EXPIRY_SWEEP_LIMIT + 5):
        _old_row(ledger, o.RETENTION_DAYS + 1)
    assert ledger.expire_stale() == EXPIRY_SWEEP_LIMIT
    assert ledger.expire_stale() == 5
    assert ledger.expire_stale() == 0


def test_pending_query_excludes_expired_before_sweep(tmp_path):
    """Leitura derivada: vencida nao conta como pendente nem sem varredura."""
    ledger = ActionLedger(tmp_path / "conscio.db")
    _old_row(ledger, o.RETENTION_DAYS + 1)
    fresh = _old_row(ledger, 1)
    ids = [r["id"] for r in ledger.pending_outcomes()]
    assert ids == [fresh]


def test_approval_queue_is_untouched(tmp_path):
    """pending() JA EXISTE e devolve a fila de aprovacao: nao pode ser
    sobrescrito por pending_outcomes()."""
    ledger = ActionLedger(tmp_path / "conscio.db")
    ledger.record(goal_fp="g", tool="bash", args_json="{}", rationale="r",
                  tier="T1", status="proposed")
    assert len(ledger.pending()) == 1
