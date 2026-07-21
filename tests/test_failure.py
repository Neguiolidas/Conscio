"""TDD tests for FailureClass enum + FailureGovernor (Conscio v3.1)."""
import pytest

from conscio.agency.adapter import AdapterTimeout, AdapterConnectionError, AdapterBadResponse
from conscio.failure import FailureClass, FailureGovernor


# ── classify ──────────────────────────────────────────────────────────

def test_classify_rate_limit():
    assert FailureGovernor.classify(Exception("429 rate limit exceeded")) is FailureClass.RATE_LIMIT


def test_classify_timeout():
    assert FailureGovernor.classify(AdapterTimeout("timed out")) is FailureClass.TIMEOUT


def test_classify_connection():
    assert FailureGovernor.classify(AdapterConnectionError("503 unavailable")) is FailureClass.PROVIDER_OUTAGE


def test_classify_bad_response():
    assert FailureGovernor.classify(AdapterBadResponse("truncated")) is FailureClass.MALFORMED_STREAM


def test_classify_permanent():
    assert FailureGovernor.classify(Exception("Unauthorized access")) is FailureClass.PERMANENT


def test_classify_stall():
    assert FailureGovernor.classify(Exception("empty response no content")) is FailureClass.STALL


# ── should_retry ──────────────────────────────────────────────────────

def test_should_retry_rate_limit():
    assert FailureGovernor.should_retry(FailureClass.RATE_LIMIT) is True


def test_should_retry_permanent():
    assert FailureGovernor.should_retry(FailureClass.PERMANENT) is False


# ── circuit breaker ───────────────────────────────────────────────────

def test_circuit_breaker_trips():
    gov = FailureGovernor(max_consecutive=3)
    for _ in range(3):
        gov.record(FailureClass.STALL, "tool_a")
    assert gov.is_open("tool_a") is True


def test_circuit_breaker_resets():
    gov = FailureGovernor(max_consecutive=3)
    for _ in range(3):
        gov.record(FailureClass.STALL, "tool_a")
    gov.reset("tool_a")
    assert gov.is_open("tool_a") is False


def test_permanent_trips_immediately():
    gov = FailureGovernor(max_consecutive=3)
    gov.record(FailureClass.PERMANENT, "tool_x")
    assert gov.is_open("tool_x") is True


def test_different_tools_independent():
    gov = FailureGovernor(max_consecutive=3)
    for _ in range(3):
        gov.record(FailureClass.STALL, "tool_a")
    assert gov.is_open("tool_a") is True
    assert gov.is_open("tool_b") is False
