"""
TokenTracker — Token estimation and savings tracking.

Records raw vs filtered token usage per source, computes savings
percentages, and tracks daily budget limits.

Uses chars/4 heuristic for token estimation (standard approximation
for English/mixed content).

Shares the same SQLite database as ContentStore and EventBus (conscio.db).

Inspired by rtk/src/core/tracking.rs — reimplemented in Python.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from .constants import DEFAULT_DB_PATH
from .sqlite_tuning import tune
from .timeutil import naive_utcnow

# ─── Constants ──────────────────────────────────────────────────────────

# Token estimation: ~4 chars per token for English/mixed content
CHARS_PER_TOKEN = 4

DEFAULT_DAILY_LIMIT = 50_000  # tokens

VALID_SOURCES = {
    "reflection", "perception", "injection", "trading",
    "system", "consciousness", "tool_output", "external",
}

# Self-bounding ledger (v3.9.4). compact() is time-based and only the dream
# calls it, but the dream is gated behind autonomy while the writer —
# reflect() — is not: an asleep host writes here forever and nothing prunes.
# A hard row cap enforced at the writer bounds the file no matter who dreams.
# The cap is checked on the first record of each process and once every
# TOKEN_LOG_PRUNE_EVERY records after that, so the steady state is at most
# MAX + PRUNE_EVERY rows and the common path stays a bare INSERT.
TOKEN_LOG_MAX = 100_000
TOKEN_LOG_PRUNE_EVERY = 1_000


# ─── TokenTracker ───────────────────────────────────────────────────────

class TokenTracker:
    """
    Token estimation and savings tracker.

    Records raw vs filtered character counts per source,
    estimates tokens (chars/4), and computes savings.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db = sqlite3.connect(str(self.db_path))
        tune(self.db, foreign_keys=True)
        self.db.row_factory = sqlite3.Row

        self.max_rows = TOKEN_LOG_MAX
        self.prune_every = TOKEN_LOG_PRUNE_EVERY
        # Start due: most writers here are short-lived (a hook, an MCP call, a
        # CLI run) and never reach an interval boundary, so a counter starting
        # at zero would let a fleet of them grow the file forever. The check is
        # O(1) when the ledger is under the cap.
        self._since_prune = self.prune_every - 1

        self._init_schema()

    # ─── Schema ──────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                raw_chars INTEGER NOT NULL,
                filtered_chars INTEGER NOT NULL,
                raw_tokens INTEGER NOT NULL,
                filtered_tokens INTEGER NOT NULL,
                saving_pct REAL NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_token_source ON token_usage(source);
            CREATE INDEX IF NOT EXISTS idx_token_timestamp ON token_usage(timestamp);
        """)
        self.db.commit()

    # ─── Core ────────────────────────────────────────────────────────

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count from text length (chars/4)."""
        return max(1, len(text) // CHARS_PER_TOKEN)

    def record(self, source: str, raw: str, filtered: str) -> dict:
        """
        Record token usage for a source.

        Args:
            source: Usage source (e.g. 'reflection', 'trading')
            raw: Original text before filtering
            filtered: Text after filtering

        Returns:
            Dict with metrics for this recording
        """
        if source not in VALID_SOURCES:
            raise ValueError(f"Invalid source '{source}'. Must be one of: {VALID_SOURCES}")

        raw_chars = len(raw)
        filtered_chars = len(filtered)
        raw_tokens = self.estimate_tokens(raw)
        filtered_tokens = self.estimate_tokens(filtered)
        saving_pct = round(
            ((raw_tokens - filtered_tokens) / raw_tokens * 100) if raw_tokens > 0 else 0.0,
            2,
        )

        timestamp = naive_utcnow().isoformat()

        self.db.execute(
            """
            INSERT INTO token_usage
                (source, raw_chars, filtered_chars, raw_tokens, filtered_tokens, saving_pct, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (source, raw_chars, filtered_chars, raw_tokens, filtered_tokens, saving_pct, timestamp),
        )
        self.db.commit()
        self._prune_if_due()

        return {
            "source": source,
            "raw_chars": raw_chars,
            "filtered_chars": filtered_chars,
            "raw_tokens": raw_tokens,
            "filtered_tokens": filtered_tokens,
            "saved_tokens": raw_tokens - filtered_tokens,
            "saving_pct": saving_pct,
        }

    def record_simple(self, source: str, raw_chars: int, filtered_chars: int) -> dict:
        """
        Record token usage from char counts (no text needed).

        Useful when you only have the counts, not the actual text.
        """
        raw = "x" * raw_chars
        filtered = "x" * filtered_chars
        return self.record(source, raw, filtered)

    # ─── Gain / Dashboard ────────────────────────────────────────────

    def gain(self, hours: int = 24) -> dict:
        """
        Savings dashboard for the last N hours.

        Returns total tokens saved, savings percentage, and breakdown by source.
        """
        cutoff = (naive_utcnow() - timedelta(hours=hours)).isoformat()

        rows = self.db.execute(
            """
            SELECT
                source,
                SUM(raw_tokens) as total_raw,
                SUM(filtered_tokens) as total_filtered,
                SUM(raw_tokens - filtered_tokens) as total_saved,
                COUNT(*) as count
            FROM token_usage
            WHERE timestamp >= ?
            GROUP BY source
            """,
            (cutoff,),
        ).fetchall()

        by_source = {}
        grand_raw = 0
        grand_filtered = 0
        grand_saved = 0

        for r in rows:
            saved = r["total_saved"]
            total_raw = r["total_raw"]
            pct = round((saved / total_raw * 100) if total_raw > 0 else 0.0, 2)
            by_source[r["source"]] = {
                "raw_tokens": r["total_raw"],
                "filtered_tokens": r["total_filtered"],
                "saved_tokens": saved,
                "saving_pct": pct,
                "recordings": r["count"],
            }
            grand_raw += r["total_raw"]
            grand_filtered += r["total_filtered"]
            grand_saved += saved

        overall_pct = round((grand_saved / grand_raw * 100) if grand_raw > 0 else 0.0, 2)

        return {
            "hours": hours,
            "total_raw_tokens": grand_raw,
            "total_filtered_tokens": grand_filtered,
            "total_saved_tokens": grand_saved,
            "overall_saving_pct": overall_pct,
            "by_source": by_source,
        }

    def budget_status(self, daily_limit: int = DEFAULT_DAILY_LIMIT) -> dict:
        """
        Daily budget status.

        Args:
            daily_limit: Max tokens per day

        Returns:
            Dict with used, remaining, and percentage of budget
        """
        cutoff = (naive_utcnow() - timedelta(hours=24)).isoformat()

        row = self.db.execute(
            """
            SELECT COALESCE(SUM(filtered_tokens), 0) as used
            FROM token_usage
            WHERE timestamp >= ?
            """,
            (cutoff,),
        ).fetchone()

        used = row["used"]
        remaining = max(0, daily_limit - used)
        pct_used = round((used / daily_limit * 100) if daily_limit > 0 else 0.0, 2)

        return {
            "daily_limit": daily_limit,
            "tokens_used": used,
            "tokens_remaining": remaining,
            "pct_used": pct_used,
            "status": "over_budget" if used > daily_limit else "ok",
        }

    # ─── Stats ───────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Overall tracker statistics."""
        total = self.db.execute("SELECT COUNT(*) as c FROM token_usage").fetchone()["c"]
        if total == 0:
            return {"total_recordings": 0}

        agg = self.db.execute(
            """
            SELECT
                SUM(raw_tokens) as total_raw,
                SUM(filtered_tokens) as total_filtered,
                SUM(raw_tokens - filtered_tokens) as total_saved,
                AVG(saving_pct) as avg_saving_pct
            FROM token_usage
            """,
        ).fetchone()

        return {
            "total_recordings": total,
            "total_raw_tokens": agg["total_raw"],
            "total_filtered_tokens": agg["total_filtered"],
            "total_saved_tokens": agg["total_saved"],
            "avg_saving_pct": round(agg["avg_saving_pct"] or 0.0, 2),
        }

    # ─── Maintenance ─────────────────────────────────────────────────

    def _prune_if_due(self) -> None:
        """Enforce the row cap on the first record and every ``prune_every``
        records after that.

        Amortized so the common path stays a single INSERT. The counter is
        in-process only, and it starts due so that a process writing fewer
        records than one interval still checks once.
        """
        self._since_prune += 1
        if self._since_prune < self.prune_every:
            return
        self._since_prune = 0
        self.enforce_cap(self.max_rows)

    def enforce_cap(self, max_rows: int) -> int:
        """Keep only the newest ``max_rows`` records. Returns rows removed.

        Complements compact(): that one applies an age policy when the mind
        dreams, this one bounds the file regardless of whether it ever does.

        The id span is an upper bound on the row count (ids are AUTOINCREMENT
        and every delete here takes the oldest), so the under-cap case costs two
        index seeks and no scan.
        """
        span = self.db.execute(
            "SELECT MIN(id) AS lo, MAX(id) AS hi FROM token_usage"
        ).fetchone()
        if span["hi"] is None or span["hi"] - span["lo"] + 1 <= max_rows:
            return 0

        cutoff = self.db.execute(
            "SELECT id FROM token_usage ORDER BY id DESC LIMIT 1 OFFSET ?",
            (max_rows,),
        ).fetchone()
        if cutoff is None:
            return 0

        cur = self.db.execute(
            "DELETE FROM token_usage WHERE id <= ?", (cutoff["id"],)
        )
        self.db.commit()
        return max(0, cur.rowcount)

    def compact(self, before_days: int = 30) -> int:
        """Remove old token usage records."""
        cutoff = (naive_utcnow() - timedelta(days=before_days)).isoformat()
        before = self.db.execute("SELECT COUNT(*) as c FROM token_usage").fetchone()["c"]
        self.db.execute("DELETE FROM token_usage WHERE timestamp < ?", (cutoff,))
        self.db.commit()
        after = self.db.execute("SELECT COUNT(*) as c FROM token_usage").fetchone()["c"]
        return before - after

    # ─── Lifecycle ───────────────────────────────────────────────────

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
