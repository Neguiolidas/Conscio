"""TDD tests for FailureClass enum + FailureGovernor (Conscio v3.1)."""

from conscio.agency.adapter import (
    AdapterBadResponse,
    AdapterConnectionError,
    AdapterHTTPError,
    AdapterTimeout,
    ProvidersExhausted,
)
from conscio.failure import FailureClass, FailureGovernor


def _exhausted(cause: Exception) -> ProvidersExhausted:
    """A chain error the way MultiProviderFallbackAdapter raises it: `from` the last provider error."""
    try:
        raise ProvidersExhausted("all 2 providers exhausted") from cause
    except ProvidersExhausted as exc:
        return exc

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


def test_classify_http_redirect():
    assert FailureGovernor.classify(AdapterHTTPError("redirect refused", status=302)) is FailureClass.PERMANENT
    assert FailureGovernor.classify(AdapterHTTPError("server error", status=500)) is FailureClass.PROVIDER_OUTAGE
    assert FailureGovernor.classify(AdapterHTTPError("rate limit", status=429)) is FailureClass.RATE_LIMIT


def test_classify_slice5_failure_classes():
    # 400, 404, 422 -> INVALID_REQUEST
    assert FailureGovernor.classify(AdapterHTTPError("bad request", status=400)) is FailureClass.INVALID_REQUEST
    assert FailureGovernor.classify(AdapterHTTPError("not found", status=404)) is FailureClass.INVALID_REQUEST
    assert FailureGovernor.classify(AdapterHTTPError("unprocessable entity", status=422)) is FailureClass.INVALID_REQUEST

    # 402 -> QUOTA
    assert FailureGovernor.classify(AdapterHTTPError("payment required", status=402)) is FailureClass.QUOTA

    # 429 with insufficient_quota or billing error_code -> QUOTA
    assert FailureGovernor.classify(AdapterHTTPError("rate limit", status=429, error_code="insufficient_quota")) is FailureClass.QUOTA
    assert FailureGovernor.classify(AdapterHTTPError("rate limit", status=429, error_code="billing_not_active")) is FailureClass.QUOTA

    # plain 429 -> RATE_LIMIT
    assert FailureGovernor.classify(AdapterHTTPError("rate limit exceeded", status=429)) is FailureClass.RATE_LIMIT


    # 401, 403, 302 -> PERMANENT
    assert FailureGovernor.classify(AdapterHTTPError("unauthorized", status=401)) is FailureClass.PERMANENT
    assert FailureGovernor.classify(AdapterHTTPError("forbidden", status=403)) is FailureClass.PERMANENT
    assert FailureGovernor.classify(AdapterHTTPError("redirect", status=302)) is FailureClass.PERMANENT

    # 503 -> PROVIDER_OUTAGE
    assert FailureGovernor.classify(AdapterHTTPError("unavailable", status=503)) is FailureClass.PROVIDER_OUTAGE

    # 418 -> MALFORMED_STREAM (unchanged)
    assert FailureGovernor.classify(AdapterHTTPError("teapot", status=418)) is FailureClass.MALFORMED_STREAM

    # should_retry is False for PERMANENT, INVALID_REQUEST, QUOTA
    assert FailureGovernor.should_retry(FailureClass.PERMANENT) is False
    assert FailureGovernor.should_retry(FailureClass.INVALID_REQUEST) is False
    assert FailureGovernor.should_retry(FailureClass.QUOTA) is False

    # should_retry is True for others
    assert FailureGovernor.should_retry(FailureClass.RATE_LIMIT) is True
    assert FailureGovernor.should_retry(FailureClass.PROVIDER_OUTAGE) is True
    assert FailureGovernor.should_retry(FailureClass.MALFORMED_STREAM) is True


def test_providers_exhausted_reports_the_last_provider_class():
    # A chain must not turn its last provider's 400 into PERMANENT: that skips
    # the gateway's T2->T3 cascade and opens the breaker for good on a 402.
    assert FailureGovernor.classify(_exhausted(AdapterHTTPError("bad request", status=400))) is FailureClass.INVALID_REQUEST
    assert FailureGovernor.classify(_exhausted(AdapterHTTPError("payment required", status=402))) is FailureClass.QUOTA
    assert FailureGovernor.classify(_exhausted(AdapterHTTPError("rate limit", status=429, error_code="insufficient_quota"))) is FailureClass.QUOTA
    assert FailureGovernor.classify(_exhausted(AdapterHTTPError("rate limit exceeded", status=429))) is FailureClass.RATE_LIMIT
    assert FailureGovernor.classify(_exhausted(AdapterHTTPError("unavailable", status=503))) is FailureClass.PROVIDER_OUTAGE


def test_providers_exhausted_without_a_cause_stays_permanent():
    assert FailureGovernor.classify(ProvidersExhausted("all 2 providers exhausted")) is FailureClass.PERMANENT


def test_classify_429_billing_in_host_without_error_code_is_rate_limit():
    # False positive prevention: a host with 'billing' in name must NOT be QUOTA without error_code
    err = AdapterHTTPError("https://billing.internal/v1/chat: HTTP 429: Too Many Requests", status=429, error_code=None)
    assert FailureGovernor.classify(err) is FailureClass.RATE_LIMIT


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


def test_a_cause_cycle_between_exhausted_chains_terminates_as_permanent():
    # classify must never raise: a hand-built __cause__ loop ends at a chain with no provider error.
    first = ProvidersExhausted("all 2 providers exhausted")
    second = ProvidersExhausted("all 2 providers exhausted")
    first.__cause__ = second
    second.__cause__ = first
    assert FailureGovernor.classify(first) is FailureClass.PERMANENT
