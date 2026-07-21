"""TokenAccount + TokenLedger (v3.1 Ato 3).

Per-task token accounting with CPM (Completions Per Million effective Tokens).
Append-only SQLite ledger. CPM = quality * 1e6 / effective_tokens, where
effective_tokens = prompt_tokens - cache_read_tokens + completion_tokens.

Mirrors the per-task accounting concept from The Harness Effect paper,
Section 4.6 and Definition 1 (token maxing is unobservable without this).
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TokenAccount:
    """Single task's token usage record."""
    task_id: int
    model: str
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    timestamp: float = 0.0


class TokenLedger:
    """Append-only token accounting backed by SQLite.

    No rewrites, no updates. rotate() deletes oldest rows beyond max_rows.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_accounts (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                duration_seconds REAL DEFAULT 0.0,
                timestamp REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def record(
        self,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_seconds: float = 0.0,
    ) -> int:
        """Append a token account record. Returns task_id."""
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute(
            """INSERT INTO token_accounts
               (model, prompt_tokens, completion_tokens,
                cache_read_tokens, cache_write_tokens,
                cost_usd, duration_seconds, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (model, prompt_tokens, completion_tokens,
             cache_read_tokens, cache_write_tokens,
             cost_usd, duration_seconds, time.time()),
        )
        task_id = cur.lastrowid
        conn.commit()
        conn.close()
        return task_id

    def effective_tokens(self) -> int:
        """Sum of (prompt - cache_read + completion) across all records."""
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens - cache_read_tokens + completion_tokens), 0) "
            "FROM token_accounts"
        )
        total = cur.fetchone()[0]
        conn.close()
        return total

    def total_tokens(self) -> int:
        """Sum of prompt_tokens + completion_tokens across all records."""
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) "
            "FROM token_accounts"
        )
        total = cur.fetchone()[0]
        conn.close()
        return total

    def total_cost(self) -> float:
        """Sum of cost_usd across all records."""
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM token_accounts"
        )
        total = cur.fetchone()[0]
        conn.close()
        return total

    def count(self) -> int:
        """Number of records."""
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute("SELECT COUNT(*) FROM token_accounts")
        count = cur.fetchone()[0]
        conn.close()
        return count

    def cpm(self, quality: float) -> float:
        """Completions Per Million effective Tokens.

        CPM = quality * 1e6 / effective_tokens.
        Returns 0.0 if no records (avoids division by zero).
        """
        eff = self.effective_tokens()
        if eff == 0:
            return 0.0
        return quality * 1e6 / eff

    def summary(self) -> dict:
        """Aggregate summary of all records."""
        return {
            "count": self.count(),
            "total_tokens": self.total_tokens(),
            "effective_tokens": self.effective_tokens(),
            "total_cost": self.total_cost(),
            "cpm_with_quality_1p0": self.cpm(quality=1.0),
        }

    def rotate(self, max_rows: int = 100000) -> int:
        """Delete oldest records beyond max_rows. Returns deleted count."""
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute("SELECT COUNT(*) FROM token_accounts")
        total = cur.fetchone()[0]
        if total <= max_rows:
            conn.close()
            return 0
        to_delete = total - max_rows
        conn.execute(
            "DELETE FROM token_accounts WHERE task_id IN "
            "(SELECT task_id FROM token_accounts ORDER BY task_id ASC LIMIT ?)",
            (to_delete,),
        )
        conn.commit()
        conn.close()
        return to_delete
