from unittest.mock import MagicMock
import pytest

from conscio.agency.adapter import (
    AdapterCaps,
    AdapterHTTPError,
    InferenceAdapter,
)
from conscio.agency.contracts import PROPOSAL_SCHEMA
from conscio.agency.gateway import GatewayError, OutputGateway


class MockAdapterWithCaps(InferenceAdapter):
    def __init__(self, caps: AdapterCaps, error: Exception | None = None):
        self._caps = caps
        self._error = error
        self.call_count = 0

    def capabilities(self) -> AdapterCaps:
        return self._caps

    def generate(self, prompt, **kwargs):
        self.call_count += 1
        if self._error:
            raise self._error
        raise RuntimeError("Unexpected call")


def test_t2_rate_limit_429_fails_fast_with_one_call():
    caps = AdapterCaps(model_name="mock", json_mode=True, grammar=False)
    adapter = MockAdapterWithCaps(
        caps, AdapterHTTPError("https://api.example.com: HTTP 429: Too Many Requests", status=429)
    )
    gw = OutputGateway(adapter)

    with pytest.raises(GatewayError) as exc_info:
        gw.request_action("prompt", PROPOSAL_SCHEMA, tool_names=["host_health"])

    assert adapter.call_count == 1
    err = exc_info.value
    assert err.infra is True
    msg = str(err).lower()
    assert "rate_limit" in msg
    assert "http 429" in msg


def test_t2_provider_outage_503_fails_fast_with_one_call():
    caps = AdapterCaps(model_name="mock", json_mode=True, grammar=False)
    adapter = MockAdapterWithCaps(
        caps, AdapterHTTPError("https://api.example.com: HTTP 503: Service Unavailable", status=503)
    )
    gw = OutputGateway(adapter)

    with pytest.raises(GatewayError) as exc_info:
        gw.request_action("prompt", PROPOSAL_SCHEMA, tool_names=["host_health"])

    assert adapter.call_count == 1
    err = exc_info.value
    assert err.infra is True
    msg = str(err).lower()
    assert "provider_outage" in msg
    assert "http 503" in msg


def test_t1_grammar_rate_limit_fails_fast_with_one_call():
    caps = AdapterCaps(model_name="mock", json_mode=True, grammar=True)
    adapter = MockAdapterWithCaps(
        caps, AdapterHTTPError("https://api.example.com: HTTP 429: Rate Limited", status=429)
    )
    gw = OutputGateway(adapter)

    with pytest.raises(GatewayError) as exc_info:
        gw.request_action("prompt", PROPOSAL_SCHEMA, tool_names=["host_health"])

    assert adapter.call_count == 1
    err = exc_info.value
    assert err.infra is True
    msg = str(err).lower()
    assert "rate_limit" in msg


def test_t2_permanent_401_fails_fast():
    caps = AdapterCaps(model_name="mock", json_mode=True, grammar=False)
    adapter = MockAdapterWithCaps(
        caps, AdapterHTTPError("https://api.example.com: HTTP 401: Unauthorized", status=401)
    )
    gw = OutputGateway(adapter)

    with pytest.raises(GatewayError) as exc_info:
        gw.request_action("prompt", PROPOSAL_SCHEMA, tool_names=["host_health"])

    assert adapter.call_count == 1
    err = exc_info.value
    assert err.infra is True
    assert str(err).startswith("permanent failure:")
