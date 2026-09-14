import sqlite3

from conscio.agency import outcome as o
from conscio.agency.ledger import ActionLedger


def _record(ledger):
    return ledger.record(goal_fp="g", tool="bash", args_json="{}",
                         rationale="r", tier="T1", status="executed")


def test_new_row_is_pending(tmp_path):
    ledger = ActionLedger(tmp_path / "conscio.db")
    row_id = _record(ledger)
    assert ledger.get(row_id)["outcome"] == o.PENDING


def test_legacy_row_stays_out_of_scope(tmp_path):
    """Linha gravada antes da feature nao vira pendencia retroativa."""
    db = tmp_path / "conscio.db"
    ledger = ActionLedger(db)
    row_id = _record(ledger)
    ledger.close()
    conn = sqlite3.connect(str(db))          # simula historico pre-v4.6
    conn.execute("UPDATE actions SET outcome='' WHERE id=?", (row_id,))
    conn.commit()
    conn.close()
    assert ActionLedger(db).get(row_id)["outcome"] == o.OUT_OF_SCOPE


def test_set_outcome_records_evidence_and_timestamp(tmp_path):
    ledger = ActionLedger(tmp_path / "conscio.db")
    row_id = _record(ledger)
    ledger.set_outcome(row_id, o.CONTRADICTED, evidence="obs:4242")
    row = ledger.get(row_id)
    assert row["outcome"] == o.CONTRADICTED
    assert row["outcome_evidence"] == "obs:4242"
    assert row["outcome_ts"] > 0


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "conscio.db"
    ActionLedger(db).close()
    ActionLedger(db).close()          # segunda abertura nao pode levantar
    ActionLedger(db).close()


def test_migration_adds_columns_to_a_pre_v46_database(tmp_path):
    """O caminho REAL da migracao: banco que nasceu sem as colunas.

    Os outros testes abrem um banco ja criado pelo schema novo, entao passam
    verdes sem nunca executar um ALTER TABLE. Este constroi o schema pre-v4.6
    a mao, com uma linha dentro, e so entao abre o ledger novo.
    """
    db = tmp_path / "conscio.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL, goal_fp TEXT NOT NULL,
            goal_text TEXT NOT NULL DEFAULT '', tool TEXT NOT NULL,
            args_json TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',
            tier TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
            verdict TEXT NOT NULL DEFAULT '',
            verdict_reasons TEXT NOT NULL DEFAULT '', ok INTEGER,
            output TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            tokens_in INTEGER NOT NULL DEFAULT 0,
            tokens_out INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            adapter TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
            approval_policy TEXT NOT NULL DEFAULT '')""")
    conn.execute("INSERT INTO actions (ts, goal_fp, tool, args_json, status)"
                 " VALUES (1.0, 'g', 'bash', '{}', 'executed')")
    conn.commit()
    conn.close()

    ledger = ActionLedger(db)                      # aqui o ALTER tem de rodar
    assert ledger.get(1)["outcome"] == o.OUT_OF_SCOPE, \
        "linha antiga virou pendencia retroativa"
    new_id = _record(ledger)
    assert ledger.get(new_id)["outcome"] == o.PENDING, \
        "linha nova nao nasceu PENDING no banco migrado"
