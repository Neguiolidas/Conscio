"""Tests for TokenAccount + TokenLedger (v3.1 Harness Efficiency Layer)."""
from __future__ import annotations

from pathlib import Path

import pytest

from conscio.token_account import TokenLedger


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_ledger.db"


class TestTokenAccountAppend:
    def test_token_account_append(self, tmp_db: Path) -> None:
        ledger = TokenLedger(tmp_db)
        task_id = ledger.record(
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
        )
        assert task_id > 0


class TestCPMCalculation:
    def test_cpm_calculation(self, tmp_db: Path) -> None:
        ledger = TokenLedger(tmp_db)
        ledger.record(
            model="gpt-4",
            prompt_tokens=1000,
            completion_tokens=100,
        )
        # effective = 1000 - 0 + 100 = 1100
        # CPM = 0.8 * 1e6 / 1100
        expected = 0.8 * 1e6 / 1100
        assert abs(ledger.cpm(quality=0.8) - expected) < 0.01


class TestCPMWithCache:
    def test_cpm_with_cache(self, tmp_db: Path) -> None:
        ledger = TokenLedger(tmp_db)
        ledger.record(
            model="gpt-4",
            prompt_tokens=1000,
            completion_tokens=100,
            cache_read_tokens=800,
        )
        # effective = 1000 - 800 + 100 = 300
        assert ledger.effective_tokens() == 300


class TestTotalCost:
    def test_total_cost(self, tmp_db: Path) -> None:
        ledger = TokenLedger(tmp_db)
        ledger.record(model="gpt-4", prompt_tokens=100, completion_tokens=50, cost_usd=0.01)
        ledger.record(model="gpt-4", prompt_tokens=200, completion_tokens=100, cost_usd=0.02)
        ledger.record(model="gpt-4", prompt_tokens=300, completion_tokens=150, cost_usd=0.03)
        assert abs(ledger.total_cost() - 0.06) < 1e-9


class TestCount:
    def test_count(self, tmp_db: Path) -> None:
        ledger = TokenLedger(tmp_db)
        for i in range(5):
            ledger.record(model="gpt-4", prompt_tokens=100, completion_tokens=50)
        assert ledger.count() == 5


class TestSummary:
    def test_summary(self, tmp_db: Path) -> None:
        ledger = TokenLedger(tmp_db)
        ledger.record(model="gpt-4", prompt_tokens=100, completion_tokens=50, cost_usd=0.01)
        ledger.record(model="gpt-4", prompt_tokens=200, completion_tokens=100, cost_usd=0.02)
        s = ledger.summary()
        assert "count" in s
        assert "total_tokens" in s
        assert "effective_tokens" in s
        assert "total_cost" in s
        assert "cpm_with_quality_1p0" in s


class TestRotate:
    def test_rotate(self, tmp_db: Path) -> None:
        ledger = TokenLedger(tmp_db)
        for i in range(10):
            ledger.record(model="gpt-4", prompt_tokens=100, completion_tokens=50)
        deleted = ledger.rotate(max_rows=5)
        assert deleted == 5
        assert ledger.count() == 5


class TestEmptyLedgerCPM:
    def test_empty_ledger_cpm(self, tmp_db: Path) -> None:
        ledger = TokenLedger(tmp_db)
        assert ledger.cpm(quality=1.0) == 0.0


class TestEffectiveTokensEmpty:
    def test_effective_tokens_empty(self, tmp_db: Path) -> None:
        ledger = TokenLedger(tmp_db)
        assert ledger.effective_tokens() == 0
