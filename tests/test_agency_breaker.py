# tests/test_agency_breaker.py
"""Tests for the minimal F1 CircuitBreaker (blueprint section 5)."""
import pytest

from conscio.agency.breaker import DEFAULT_MAX_RETRIES, CircuitBreaker
from conscio.agency.ledger import ActionLedger


class _FakeBus:
    def __init__(self):
        self.events = []

    def emit(self, **kw):
        self.events.append(kw)
        return 1


@pytest.fixture
def parts(tmp_path):
    ledger = ActionLedger(tmp_path / "conscio.db")
    bus = _FakeBus()
    breaker = CircuitBreaker(ledger, bus)
    yield ledger, bus, breaker
    ledger.close()


def _fail(ledger, goal_fp="g"):
    ledger.record(goal_fp=goal_fp, tool="t", args_json="{}", rationale="r",
                  tier="T2", status="failed")


class TestBreaker:
    def test_default_threshold_is_three(self):
        assert DEFAULT_MAX_RETRIES == 3

    def test_allows_below_threshold(self, parts):
        ledger, _, breaker = parts
        _fail(ledger)
        _fail(ledger)
        assert breaker.should_trip("g") is False

    def test_trips_at_threshold(self, parts):
        ledger, _, breaker = parts
        for _ in range(DEFAULT_MAX_RETRIES):
            _fail(ledger)
        assert breaker.should_trip("g") is True

    def test_trip_emits_intractable_dissonance_error(self, parts):
        _, bus, breaker = parts
        breaker.trip("g", detail="fs_read keeps failing")
        event = bus.events[0]
        assert event["type"] == "error" and event["category"] == "system"
        assert "Intractable dissonance" in event["data"]["message"]
        assert "fs_read keeps failing" in event["data"]["message"]
        assert event["data"]["goal_fp"] == "g"

    def test_success_resets_streak(self, parts):
        ledger, _, breaker = parts
        for _ in range(DEFAULT_MAX_RETRIES):
            _fail(ledger)
        ledger.record(goal_fp="g", tool="t", args_json="{}", rationale="r",
                      tier="T2", status="executed", ok=True)
        assert breaker.should_trip("g") is False
