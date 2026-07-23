"""Tests for MultiProviderFallbackAdapter — multi-provider fallback chain."""
from unittest.mock import MagicMock
import pytest

from conscio.agency.adapter import (
    AdapterBadResponse,
    AdapterError,
    AdapterTimeout,
    InferenceResult,
)
from conscio.agency.fallback_multi import (
    MultiProviderFallbackAdapter,
    ProviderConfig,
)


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
