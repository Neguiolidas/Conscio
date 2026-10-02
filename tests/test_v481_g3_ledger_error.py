import json
import sqlite3
from unittest.mock import MagicMock

from conscio.agency.act import ActPipeline, ActStatus
from conscio.agency.adapter import AdapterCaps, AdapterHTTPError, InferenceAdapter
from conscio.agency.breaker import CircuitBreaker
from conscio.agency.fingerprint import goal_fingerprint
from conscio.agency.ledger import ActionLedger
from conscio.agency.tools import Risk, ToolRegistry
from conscio.context_manager import ConsciousnessState


class _FakeBus:
    def __init__(self):
        self.events = []

    def emit(self, **kw):
        self.events.append(kw)
        return 1


class _MockAdapter(InferenceAdapter):
    def __init__(self, exc=None, text=""):
        self._exc = exc
        self._text = text

    def capabilities(self):
        return AdapterCaps(model_name="mock", json_mode=True)

    def generate(self, prompt, **kwargs):
        if self._exc:
            raise self._exc
        return MagicMock(text=self._text, tokens_in=10, tokens_out=10, latency_ms=10)


def test_ledger_record_stores_error_column(tmp_path):
    ledger = ActionLedger(tmp_path / "ledger.db")
    row_id = ledger.record(
        goal_fp="fp123",
        tool="(none)",
        args_json="{}",
        rationale="",
        tier="T2",
        status="failed",
        error="rate_limit: HTTP 429",
    )

    row = ledger.get(row_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["tool"] == "(none)"
    assert row["error"] == "rate_limit: HTTP 429"


def test_act_records_gateway_error_in_ledger_without_tripping_breaker(tmp_path):
    ledger = ActionLedger(tmp_path / "ledger.db")
    bus = _FakeBus()
    breaker = CircuitBreaker(ledger, bus, max_retries=1, db_path=tmp_path / "breaker.db")
    registry = ToolRegistry()
    registry.register(
        "host_health",
        lambda: "ok",
        params={},
        risk=Risk.LOW,
        description="check health",
    )

    adapter = _MockAdapter(
        exc=AdapterHTTPError(
            "https://api.example.com: HTTP 429: Too Many Requests", status=429
        )
    )
    pipeline = ActPipeline(
        adapter=adapter,
        registry=registry,
        ledger=ledger,
        breaker=breaker,
        emit_fn=bus.emit,
    )

    goal_text = "Test goal 1"
    goal_fp = goal_fingerprint(goal_text)
    state = ConsciousnessState(state_summary="s", active_goals=[goal_text], coherence_note="c")
    report = pipeline.act(state)

    assert report.status is ActStatus.FAILED

    # Check ledger record
    conn = sqlite3.connect(tmp_path / "ledger.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM actions WHERE goal_fp = ?", (goal_fp,)).fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert row["tool"] == "(none)"
    assert "gateway:" in row["error"]
    assert "rate_limit" in row["error"]
    assert "HTTP 429" in row["error"]

    # Check circuit breaker did NOT quarantine because infra=True
    assert not breaker.is_quarantined(goal_fp)


def test_act_records_unknown_tool_in_ledger_and_counts_breaker(tmp_path):
    ledger = ActionLedger(tmp_path / "ledger.db")
    bus = _FakeBus()
    breaker = CircuitBreaker(ledger, bus, max_retries=1, db_path=tmp_path / "breaker.db")
    registry = ToolRegistry()

    adapter = _MockAdapter(
        text=json.dumps(
            {
                "tool": "non_existent_tool",
                "args": {},
                "rationale": "r",
                "expected_outcome": "e",
            }
        )
    )
    pipeline = ActPipeline(
        adapter=adapter,
        registry=registry,
        ledger=ledger,
        breaker=breaker,
        emit_fn=bus.emit,
    )

    goal_text = "Test goal 2"
    goal_fp = goal_fingerprint(goal_text)
    state = ConsciousnessState(state_summary="s", active_goals=[goal_text], coherence_note="c")
    report = pipeline.act(state)

    assert report.status is ActStatus.FAILED

    conn = sqlite3.connect(tmp_path / "ledger.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM actions WHERE goal_fp = ?", (goal_fp,)).fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert row["tool"] == "non_existent_tool"
    assert "unknown tool 'non_existent_tool'" in row["error"]

    # Breaker quarantined goal because infra=False and failures >= max_retries (1)
    assert breaker.is_quarantined(goal_fp)
