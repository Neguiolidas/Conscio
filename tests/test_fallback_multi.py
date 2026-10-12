"""Tests for MultiProviderFallbackAdapter — multi-provider fallback chain."""
import email.message
import urllib.error
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest

from conscio.agency.adapter import (
    AdapterBadResponse,
    AdapterError,
    AdapterHTTPError,
    AdapterTimeout,
    InferenceResult,
    ProvidersExhausted,
)
from conscio.agency.fallback_multi import (
    MultiProviderFallbackAdapter,
    ProviderConfig,
)
from conscio.failure import FailureClass, FailureGovernor


def _mock_adapter(text="OK", latency=10):
    m = MagicMock()
    m.generate.return_value = InferenceResult(
        text=text, tokens_in=10, tokens_out=5, latency_ms=latency)
    m.capabilities.return_value = MagicMock()
    return m


def test_first_provider_works():
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "m1", "base_url": "u1"},
                   {"model": "m2", "base_url": "u2"}],
        backoff_base=0.01)
    a1, a2 = _mock_adapter("primary"), _mock_adapter("secondary")
    fp._adapters = [a1, a2]
    result = fp.generate("test")
    assert result.text == "primary"
    assert fp.current_model == "m1"
    a1.generate.assert_called_once()
    a2.generate.assert_not_called()


def test_falls_to_second_provider():
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "m1", "base_url": "u1"},
                   {"model": "m2", "base_url": "u2"}],
        retry_per_provider=1, backoff_base=0.01)
    a1 = MagicMock()
    a1.generate.side_effect = AdapterTimeout("timeout")
    a2 = _mock_adapter("secondary")
    fp._adapters = [a1, a2]
    result = fp.generate("test")
    assert result.text == "secondary"
    assert fp.current_model == "m2"


def test_all_providers_exhausted():
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "m1", "base_url": "u1"},
                   {"model": "m2", "base_url": "u2"}],
        retry_per_provider=1, backoff_base=0.01)
    a1 = MagicMock()
    a1.generate.side_effect = AdapterTimeout("timeout")
    a2 = MagicMock()
    a2.generate.side_effect = AdapterBadResponse("HTTP 500")
    fp._adapters = [a1, a2]
    with pytest.raises(AdapterError, match="2 providers exhausted"):
        fp.generate("test")


def test_retry_within_provider():
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "m1", "base_url": "u1"}],
        retry_per_provider=2, backoff_base=0.01)
    a1 = MagicMock()
    a1.generate.side_effect = [
        AdapterTimeout("timeout"),
        InferenceResult(text="recovered", tokens_in=1, tokens_out=1, latency_ms=5),
    ]
    fp._adapters = [a1]
    result = fp.generate("test")
    assert result.text == "recovered"
    assert a1.generate.call_count == 2


def test_empty_providers_raises():
    with pytest.raises(ValueError, match="at least 1 provider"):
        MultiProviderFallbackAdapter(providers=[])


def test_current_model_after_fallback():
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "m1", "base_url": "u1"},
                   {"model": "m2", "base_url": "u2"}],
        retry_per_provider=1, backoff_base=0.01)
    a1 = MagicMock()
    a1.generate.side_effect = AdapterTimeout("timeout")
    a2 = _mock_adapter("ok")
    fp._adapters = [a1, a2]
    fp.generate("test")
    assert fp.current_model == "m2"


def test_provider_config_dataclass():
    cfg = ProviderConfig(model="test", base_url="http://localhost:8080/v1")
    assert cfg.model == "test"
    assert cfg.api_key == ""
    assert cfg.timeout == 120.0


def test_connection_error_triggers_fallback():
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "m1", "base_url": "u1"},
                   {"model": "m2", "base_url": "u2"}],
        retry_per_provider=1, backoff_base=0.01)
    a1 = MagicMock()
    a1.generate.side_effect = ConnectionError("refused")
    a2 = _mock_adapter("ok")
    fp._adapters = [a1, a2]
    result = fp.generate("test")
    assert result.text == "ok"


def test_three_providers_chain():
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "p1", "base_url": "u1"},
                   {"model": "p2", "base_url": "u2"},
                   {"model": "p3", "base_url": "u3"}],
        retry_per_provider=1, backoff_base=0.01)
    a1 = MagicMock()
    a1.generate.side_effect = AdapterTimeout("t1")
    a2 = MagicMock()
    a2.generate.side_effect = AdapterBadResponse("500")
    a3 = _mock_adapter("third")
    fp._adapters = [a1, a2, a3]
    result = fp.generate("test")
    assert result.text == "third"
    assert fp.current_model == "p3"


def test_permanent_failure_skips_retry():
    from conscio.agency.adapter import AdapterHTTPError
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "p1", "base_url": "u1"},
                   {"model": "p2", "base_url": "u2"}],
        retry_per_provider=3, backoff_base=0.01)
    a1 = MagicMock()
    # 401 is classified as FailureClass.PERMANENT
    a1.generate.side_effect = AdapterHTTPError("unauthorized", status=401)
    a2 = _mock_adapter("recovered")
    fp._adapters = [a1, a2]
    result = fp.generate("test")
    assert result.text == "recovered"
    # a1 should only be attempted once, not 3 times
    assert a1.generate.call_count == 1


def test_fallback_adapter_returns_to_primary_after_3_successes():
    from conscio.agency.fallback_adapter import FallbackAdapter
    fa = FallbackAdapter(base_url="http://localhost:1234/v1", models=["m1", "m2"])
    a1 = _mock_adapter("a1-reply")
    a2 = _mock_adapter("a2-reply")
    fa._adapters = [a1, a2]

    # Force a1 to fail once so it advances to m2
    a1.generate.side_effect = AdapterBadResponse("bad")
    res = fa.generate("prompt 1")
    assert res.text == "a2-reply"
    assert fa.current_model == "m2"
    assert fa._consecutive_successes == 1

    # Restore a1 so it would succeed if called
    a1.generate.side_effect = None
    a1.generate.return_value = InferenceResult(text="a1-reply", tokens_in=10, tokens_out=5, latency_ms=10)

    # Success 2 on m2
    res = fa.generate("prompt 2")
    assert res.text == "a2-reply"
    assert fa.current_model == "m2"
    assert fa._consecutive_successes == 2

    # Success 3 on m2 triggers reset to m1
    res = fa.generate("prompt 3")
    assert res.text == "a2-reply"
    assert fa.current_model == "m1"
    assert fa._consecutive_successes == 0

    # Next generate uses m1 (primary)
    res = fa.generate("prompt 4")
    assert res.text == "a1-reply"
    assert fa.current_model == "m1"


# ── Slice 5: non-retryable 4xx and Retry-After ─────────────────────────────

@pytest.mark.parametrize("status", [400, 402])
def test_slice5_non_retryable_http_skips_retry(status):
    from conscio.agency.adapter import AdapterHTTPError
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "p1", "base_url": "u1"},
                   {"model": "p2", "base_url": "u2"}],
        retry_per_provider=3, backoff_base=0.01)
    a1 = MagicMock()
    a1.generate.side_effect = AdapterHTTPError(f"HTTP {status}", status=status)
    a2 = _mock_adapter("recovered")
    fp._adapters = [a1, a2]
    result = fp.generate("test")
    assert result.text == "recovered"
    assert a1.generate.call_count == 1


def test_slice5_retry_after_honored_when_within_cap(monkeypatch):
    from conscio.agency.adapter import AdapterHTTPError
    slept = []
    monkeypatch.setattr("conscio.agency.fallback_multi.time.sleep", lambda s: slept.append(s))
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "p1", "base_url": "u1"}],
        retry_per_provider=2, backoff_base=0.01, backoff_max=10.0)
    a1 = MagicMock()
    a1.generate.side_effect = [
        AdapterHTTPError("rate limit", status=429, retry_after=0.5),
        InferenceResult(text="recovered", tokens_in=1, tokens_out=1, latency_ms=5),
    ]
    fp._adapters = [a1]
    res = fp.generate("test")
    assert res.text == "recovered"
    assert len(slept) == 1
    assert slept[0] >= 0.5


def test_slice5_retry_after_exceeds_cap_advances_without_sleep(monkeypatch):
    from conscio.agency.adapter import AdapterHTTPError
    slept = []
    monkeypatch.setattr("conscio.agency.fallback_multi.time.sleep", lambda s: slept.append(s))
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "p1", "base_url": "u1"},
                   {"model": "p2", "base_url": "u2"}],
        retry_per_provider=3, backoff_base=0.01, backoff_max=10.0)
    a1 = MagicMock()
    a1.generate.side_effect = AdapterHTTPError("rate limit", status=429, retry_after=30.0)
    a2 = _mock_adapter("p2-recovered")
    fp._adapters = [a1, a2]
    res = fp.generate("test")
    assert res.text == "p2-recovered"
    assert not any(s >= 30.0 for s in slept)
    assert a1.generate.call_count == 1
    assert a2.generate.call_count == 1


def _chain_failing_with(status, error_code=None):
    """Two providers, each rejecting the request with the same HTTP status."""
    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "m1", "base_url": "u1"},
                   {"model": "m2", "base_url": "u2"}],
        retry_per_provider=1, backoff_base=0.01)
    adapters = []
    for _ in range(2):
        m = MagicMock()
        m.generate.side_effect = AdapterHTTPError(
            f"HTTP {status}", status=status, error_code=error_code)
        adapters.append(m)
    fp._adapters = adapters
    return fp


def test_exhausted_chain_of_400s_is_classified_as_invalid_request():
    with pytest.raises(AdapterError) as exc:
        _chain_failing_with(400).generate("test")
    assert FailureGovernor.classify(exc.value) is FailureClass.INVALID_REQUEST


def test_exhausted_chain_of_402s_is_classified_as_quota():
    with pytest.raises(AdapterError) as exc:
        _chain_failing_with(402).generate("test")
    assert FailureGovernor.classify(exc.value) is FailureClass.QUOTA


def test_exhausted_quota_chain_does_not_open_the_breaker_permanently():
    with pytest.raises(AdapterError) as exc:
        _chain_failing_with(402).generate("test")
    gov = FailureGovernor(max_consecutive=3)
    gov.record(FailureGovernor.classify(exc.value), "tool")
    assert gov.is_open("tool") is False





class _BrokenBody:
    """Error body whose every read raises the given exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def read(self, *args, **kwargs) -> bytes:
        raise self._exc

    def close(self) -> None:
        """No-op: nothing to release."""


def test_a_429_body_that_cannot_be_read_still_advances_the_chain():
    """The body read may fail with any exception; the chain must still reach provider 2."""

    def reject(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 429, "Too Many Requests", email.message.EmailMessage(),
            _BrokenBody(RuntimeError("body stream broke")))

    fp = MultiProviderFallbackAdapter(
        providers=[{"model": "m1", "base_url": "http://u1"},
                   {"model": "m2", "base_url": "http://u2"}],
        retry_per_provider=1, backoff_base=0.01)

    with (
        patch("conscio.agency.adapters.open_no_redirect", side_effect=reject) as opened,
        pytest.raises(ProvidersExhausted) as exc_info,
    ):
        fp.generate("test")

    hosts = [urlsplit(call.args[0].full_url).netloc for call in opened.call_args_list]
    assert hosts == ["u1", "u2"]
    assert FailureGovernor.classify(exc_info.value) is FailureClass.RATE_LIMIT
