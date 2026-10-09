# conscio/agency/ledger.py
"""
ActionLedger — append-only audit of every act() cycle (spec section 5.9,
safety rule R8). Lives in the EXISTING shared conscio.db (the same WAL
database that holds ContentStore + EventBus) — no new DB convention.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

# Fonte unica em honesty/: o hook de Stop roda a mesma varredura e nao pode
# importar este modulo. Reexportado aqui pelo nome que o resto do pacote usa.
from ..honesty.sweep import EXPIRY_SWEEP_LIMIT
from ..sqlite_tuning import tune
from .outcome import PENDING

_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    goal_fp TEXT NOT NULL,
    goal_text TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,              -- proposed|executing|executed|rejected|failed|locked
    verdict TEXT NOT NULL DEFAULT '',  -- skeptic verdict (F2)
    verdict_reasons TEXT NOT NULL DEFAULT '',
    ok INTEGER,                        -- NULL until executed
    output TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    adapter TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    approval_policy TEXT NOT NULL DEFAULT '',  -- v2.0.1: host-act gate
    outcome TEXT NOT NULL DEFAULT '',          -- v4.6: '' = fora de escopo
    outcome_ts REAL,
    outcome_evidence TEXT NOT NULL DEFAULT '', -- ponteiro, nunca texto livre
    is_infra INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_actions_goal ON actions(goal_fp, id);
CREATE INDEX IF NOT EXISTS idx_actions_tool ON actions(tool);
"""


class ActionLedger:
    def __init__(self, db_path: Path | str):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        tune(self._conn, durable=True)
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        try:                                   # F1 databases lack the column
            self._conn.execute("ALTER TABLE actions ADD COLUMN"
                               " verdict_reasons TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass                               # already present
        try:                                   # F1-F3 databases lack goal_text
            self._conn.execute("ALTER TABLE actions ADD COLUMN"
                               " goal_text TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass                               # already present
        try:                                   # v2.0.1 databases lack approval_policy
            self._conn.execute("ALTER TABLE actions ADD COLUMN"
                               " approval_policy TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass                               # already present
        for column, decl in (("outcome", "TEXT NOT NULL DEFAULT ''"),
                             ("outcome_ts", "REAL"),
                             ("outcome_evidence", "TEXT NOT NULL DEFAULT ''"),
                             ("is_infra", "INTEGER NOT NULL DEFAULT 0")):
            try:                               # v4.6 / v4.9: bancos anteriores nao tem
                self._conn.execute(
                    f"ALTER TABLE actions ADD COLUMN {column} {decl}")
                self._conn.commit()
            except sqlite3.OperationalError:
                pass                           # already present
        # O indice vem DEPOIS dos ALTER, nunca no _SCHEMA: num banco pre-v4.6 a
        # tabela ja existe (CREATE IF NOT EXISTS nao faz nada) e o indice
        # referenciaria uma coluna que so o ALTER acrescenta -- o ledger nao
        # abriria mais em nenhuma instalacao existente.
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_outcome"
                           " ON actions(outcome, ts)")
        self._conn.commit()

    def record(self, *, goal_fp: str, tool: str, args_json: str,
               rationale: str, tier: str, status: str, ok: bool | None = None,
               tokens_in: int = 0, tokens_out: int = 0,
               adapter: str = "", model: str = "",
               goal_text: str = "", approval_policy: str = "",
               error: str = "", is_infra: bool = False) -> int:
        # BUG-48: executed_since filters ok=1, but record(status='executed')
        # without an explicit ok= argument left ok=NULL. Distill reads
        # executed_since, so skills were never generated. Default ok=True
        # when the caller signals success via status='executed' and does
        # not pass an explicit ok.
        if ok is None and status == "executed":
            ok = True
        cur = self._conn.execute(
            "INSERT INTO actions (ts, goal_fp, goal_text, tool, args_json,"
            " rationale, tier, status, ok, tokens_in, tokens_out, adapter,"
            " model, approval_policy, outcome, error, is_infra)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), goal_fp, goal_text, tool, args_json, rationale,
             tier, status, None if ok is None else int(ok), tokens_in,
             tokens_out, adapter, model, approval_policy, PENDING, error,
             int(is_infra)))
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def tool_outcome_counts(self, tool: str) -> tuple[int, int]:
        """(attempts, successes) of executed rows for one tool (v4.7).

        Feeds the per-tool Beta posterior in act. Counts only rows with a
        non-null ok; status != 'executed' never happened.
        """
        row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(ok), 0) FROM actions"
            " WHERE tool=? AND status='executed' AND ok IS NOT NULL",
            (tool,)).fetchone()
        return int(row[0]), int(row[1])

    def contradiction_count(self, tool: str) -> int:
        """Count how many CONTRADICTED outcomes exist for this tool."""
        from .outcome import CONTRADICTED
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM actions WHERE tool=? AND outcome=?",
            (tool, CONTRADICTED))
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def update_execution(self, row_id: int, *, ok: bool, output: str,
                         error: str, duration_ms: int, status: str) -> None:
        self._conn.execute(
            "UPDATE actions SET ok=?, output=?, error=?, duration_ms=?,"
            " status=? WHERE id=?",
            (int(ok), output, error, duration_ms, status, row_id))
        self._conn.commit()

    def expire_stale(self, now: float | None = None,
                     limit: int = EXPIRY_SWEEP_LIMIT) -> int:
        """Materializa pendencias vencidas como UNSUPPORTED.

        Delega em ``honesty.sweep``, que e a implementacao unica: o hook de
        Stop roda a mesma varredura sem poder importar este modulo.
        """
        from ..honesty.sweep import expire_stale as _sweep
        return _sweep(self._conn, now, limit)

    def pending_outcomes(self, limit: int = 50) -> list[dict]:
        """Pendencias ainda decidiveis.

        A janela e aplicada na CONSULTA, nao so pela varredura: numa maquina
        parada ha meses a varredura nunca rodou e a linha vencida continuaria
        se apresentando como pendente. Nome distinto de ``pending()``, que ja
        existe e devolve a fila de aprovacao (status='proposed').
        """
        from .outcome import RETENTION_DAYS
        cutoff = time.time() - RETENTION_DAYS * 86400
        rows = self._conn.execute(
            "SELECT * FROM actions WHERE outcome=? AND ts >= ?"
            " ORDER BY id DESC LIMIT ?", (PENDING, cutoff, limit)).fetchall()
        return [dict(r) for r in rows]

    def set_outcome(self, row_id: int, outcome: str,
                    evidence: str = "") -> None:
        """Grava o desfecho. ``evidence`` e ponteiro (``obs:<id>`` ou hash de
        blob), nunca prosa: texto livre reproduziria o defeito do verify()."""
        self._conn.execute(
            "UPDATE actions SET outcome=?, outcome_ts=?, outcome_evidence=?"
            " WHERE id=?", (outcome, time.time(), evidence, row_id))
        self._conn.commit()

    def claim(self, row_id: int) -> bool:
        """Atomically transition proposed -> executing.

        Returns True iff this call won the claim. Closes the approve()
        double-execution window: only the winner dispatches the tool. A
        crash after claim leaves the row 'executing' (not 'proposed'), so
        it is never re-approved — a visible, safe stuck state.
        """
        cur = self._conn.execute(
            "UPDATE actions SET status='executing'"
            " WHERE id=? AND status='proposed'", (row_id,))
        self._conn.commit()
        return cur.rowcount == 1

    def update_verdict(self, row_id: int, verdict: str,
                       reasons: list[str]) -> None:
        self._conn.execute(
            "UPDATE actions SET verdict=?, verdict_reasons=? WHERE id=?",
            (verdict, "; ".join(reasons), row_id))
        self._conn.commit()

    def pending(self, limit: int = 20) -> list[dict]:
        """Approval queue (R6): proposals awaiting approve()/reject()."""
        rows = self._conn.execute(
            "SELECT * FROM actions WHERE status='proposed'"
            " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def has_in_flight(self) -> bool:
        """True iff any action is still proposed or executing (v2.0.1:
        blocks a host-act manifest swap mid-flight)."""
        row = self._conn.execute(
            "SELECT 1 FROM actions WHERE status IN ('proposed','executing')"
            " LIMIT 1").fetchone()
        return row is not None

    def get(self, row_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM actions WHERE id=?", (row_id,)).fetchone()
        return dict(row) if row else None

    def latest(self, n: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM actions ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def count(self, task_type: str | None = None) -> int:
        if task_type:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM actions WHERE tool=?",
                (task_type,)).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM actions").fetchone()
        return int(row[0])

    def executed_since(self, after_id: int) -> list[dict]:
        """Successful executions newer than `after_id`, oldest first
        (the Distill sub-phase's feed — spec v1.1 section 4)."""
        rows = self._conn.execute(
            "SELECT id, goal_fp, goal_text, tool, args_json, rationale"
            " FROM actions WHERE id > ? AND status='executed' AND ok=1"
            " ORDER BY id ASC", (after_id,)).fetchall()
        return [dict(r) for r in rows]

    def nth_recent_ts(self, n: int) -> float:
        """ts of the nth most recent row; 0.0 when fewer rows exist."""
        row = self._conn.execute(
            "SELECT ts FROM actions ORDER BY id DESC LIMIT 1 OFFSET ?",
            (max(0, n - 1),)).fetchone()
        return float(row["ts"]) if row else 0.0

    def consecutive_failures(self, goal_fp: str) -> int:
        """Trailing run of status='failed' rows for this goal, ignoring infra errors."""
        rows = self._conn.execute(
            "SELECT status, is_infra FROM actions WHERE goal_fp=? ORDER BY id DESC"
            " LIMIT 50", (goal_fp,)).fetchall()
        streak = 0
        for row in rows:
            if row["status"] == "failed":
                if row["is_infra"]:
                    continue
                streak += 1
            else:
                break
        return streak

    # ── v4.8.1 (batch H): calibration queries ─────────────────────────────
    # Public API so calibration code never touches the private _conn.
    # Every attempt counts (executed/failed/rejected): a failed attempt
    # burned provider quota exactly like a successful one.

    def last_attempt_ts(self, goal_fp: str) -> float | None:
        """ts of the most recent row for this goal fingerprint, whatever
        its status; None when the goal never reached the ledger."""
        row = self._conn.execute(
            "SELECT ts FROM actions WHERE goal_fp=?"
            " ORDER BY id DESC LIMIT 1", (goal_fp,)).fetchone()
        return float(row[0]) if row else None

    def max_id(self) -> int:
        """Current highest row id — run-scoped counting baseline."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM actions").fetchone()
        return int(row[0])

    def count_since_id(self, goal_fp: str, after_id: int) -> int:
        """Rows of this goal fingerprint newer than `after_id` — how many
        cycles of this goal a run has already consumed."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM actions WHERE goal_fp=? AND id > ?",
            (goal_fp, after_id)).fetchone()
        return int(row[0])

    def count_attempts_since(self, ts: float, *, now: float | None = None) -> int:
        """AUTONOMOUS-LOOP attempt rows in the window (ts, now].

        v4.8.1 (batch H round 6, #866): counts actions rows of the AWAKE
        pipeline only — tier != 'host'. Every such row is one attempt
        downstream of gateway.request_action, which paid for at least
        one LLM request whatever the outcome (executed, failed,
        rejected). A row with tokens=0 in a 429 storm still burned RPM;
        the first cut filtered on (tokens_in>0 OR tokens_out>0) and was
        blind to exactly the failure storm it existed to cap.

        Why tier != 'host' (round-6 review): the actions table is
        shared by the whole space — the MCP server's host_act channel
        writes its own rows into the same conscio.db the ceiling
        reads. The ceiling is the budget of the AUTONOMOUS loop, so
        host-originated spend (audit calls, rejected proposals) is the
        HOST's budget, outside awake's control. Two facts close the
        case: host_act._reject writes tier='host' rows with ZERO LLM
        calls (a host spamming malformed proposals must not lock the
        awake loop for 24h — the inverse of what the ceiling protects),
        and host rows land exactly in the window the ceiling watches
        because _gate only accepts host proposals while the engine is
        AWAKE. Grep of every writer to actions: act.py:223/419
        (tier=last_tier or 'T2' — T1/T2/T3 only: an empty last_tier
        falls back to 'T2', never 'host'), host_act.py:51/88
        (tier='host'), bench.py:380/386 (offline harness,
        tier=gateway.last_tier, which may be ''), gateway.py:182 is the
        token_ledger, a different table.
        """
        now = time.time() if now is None else now
        row = self._conn.execute(
            "SELECT COUNT(*) FROM actions"
            " WHERE ts > ? AND ts <= ? AND tier != 'host'",
            (float(ts), float(now))).fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()
