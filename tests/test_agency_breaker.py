# tests/test_agency_breaker.py
"""Tests for the minimal F1 CircuitBreaker (blueprint section 5)."""
import time as _time

import pytest

from conscio.agency.breaker import (DEFAULT_MAX_RETRIES,
                                    GLOBAL_LOCKDOWN_QUORUM, CircuitBreaker)
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


# ── F2: quarantine + dynamic threshold ──────────────────────────────────


class _Bus:
    def __init__(self):
        self.events = []

    def emit(self, **kw):
        self.events.append(kw)

    def query(self, **kw):
        return list(self.events)


class _Trust:
    def __init__(self, retries):
        self.retries = retries

    def max_action_retries(self, task_type):
        return self.retries


def _failed_rows(ledger, goal_fp, n):
    for _ in range(n):
        ledger.record(goal_fp=goal_fp, tool="t", args_json="{}",
                      rationale="", tier="T2", status="failed")


def test_dynamic_threshold_from_trust(tmp_path):
    led = ActionLedger(tmp_path / "conscio.db")
    brk = CircuitBreaker(led, _Bus(), trust=_Trust(2),
                         db_path=tmp_path / "conscio.db")
    _failed_rows(led, "g1", 2)
    assert brk.should_trip("g1", task_type="t") is True
    assert brk.should_trip("g1") is False        # no task_type -> F1 fallback 3
    brk.close()
    led.close()


def test_trip_quarantines_goal_not_whole_agent(tmp_path):
    led = ActionLedger(tmp_path / "conscio.db")
    brk = CircuitBreaker(led, _Bus(), db_path=tmp_path / "conscio.db")
    _failed_rows(led, "g1", 3)
    brk.trip("g1", detail="boom", goal_text="organize sandbox files")
    assert brk.is_quarantined("g1") is True
    assert brk.global_lockdown_due() is False    # 1 < quorum
    brk.close()
    led.close()


def test_global_lockdown_at_quorum(tmp_path):
    led = ActionLedger(tmp_path / "conscio.db")
    brk = CircuitBreaker(led, _Bus(), db_path=tmp_path / "conscio.db")
    for i in range(GLOBAL_LOCKDOWN_QUORUM):
        fp = f"g{i}"
        _failed_rows(led, fp, 3)
        brk.trip(fp, goal_text=f"goal number {i}")
    assert brk.global_lockdown_due() is True
    brk.close()
    led.close()


def test_no_db_falls_back_to_f1_global_lockdown(tmp_path):
    led = ActionLedger(tmp_path / "conscio.db")
    brk = CircuitBreaker(led, _Bus())            # F1 construction, no db_path
    _failed_rows(led, "g1", 3)
    brk.trip("g1")
    assert brk.global_lockdown_due() is True     # paralysis = global, as in F1
    led.close()


def test_cooldown_release(tmp_path):
    led = ActionLedger(tmp_path / "conscio.db")
    brk = CircuitBreaker(led, _Bus(), db_path=tmp_path / "conscio.db",
                         cooldown_s=0.0)         # expires immediately
    _failed_rows(led, "g1", 3)
    brk.trip("g1", goal_text="anything")
    _time.sleep(0.01)
    released = brk.review_quarantine()
    assert released == ["g1"]
    assert brk.is_quarantined("g1") is False
    brk.close()
    led.close()


def test_relevant_event_releases_quarantine(tmp_path):
    led = ActionLedger(tmp_path / "conscio.db")
    bus = _Bus()
    brk = CircuitBreaker(led, bus, db_path=tmp_path / "conscio.db",
                         cooldown_s=9999.0)
    _failed_rows(led, "g1", 3)
    brk.trip("g1", goal_text="reconcile the sandbox inventory")
    # a fresh reflection event mentioning the goal's domain arrives
    bus.events.append({"type": "reflection", "category": "consciousness",
                       "data": {"summary": "new inventory facts learned"}})
    released = brk.review_quarantine()
    assert released == ["g1"]
    brk.close()
    led.close()


def test_try_break_relevant_event_window_is_tz_correct(tmp_path, monkeypatch):
    """B-007: _relevant_event_since builds its window from an epoch; a naive-LOCAL
    conversion skews it vs the naive-UTC event store (same class as B-003b). Under a
    non-UTC TZ a genuinely-recent relevant event must still be found. Uses a REAL
    EventBus (the _Bus fake ignores `since`, so it can't see this bug)."""
    import time as _t

    from conscio.event_bus import EventBus

    monkeypatch.setenv("TZ", "Asia/Tokyo")                # UTC+9
    _t.tzset()
    bus = EventBus(db_path=tmp_path / "conscio.db")
    led = ActionLedger(tmp_path / "conscio.db")
    brk = CircuitBreaker(led, bus)
    try:
        bus.emit(type="reflection", category="consciousness",
                 data={"summary": "refactor the parser module"})
        locked_at = _t.time() - 5                          # window opened 5s ago
        # OLD naive-local 'since' lands ~9h in the FUTURE -> 0 events -> False
        assert brk._relevant_event_since("refactor parser", locked_at) is True
    finally:
        brk.close()
        led.close()
        bus.close()
        monkeypatch.delenv("TZ", raising=False)
        _t.tzset()
