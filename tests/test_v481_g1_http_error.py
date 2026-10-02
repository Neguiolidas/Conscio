import io
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch
import pytest

from conscio.agency.adapter import (
    AdapterBadResponse,
    AdapterHTTPError,
    InferenceResult,
)
from conscio.agency.adapters import _post_json
from conscio.agency.fallback_adapter import FallbackAdapter
from conscio.agency.fallback_multi import MultiProviderFallbackAdapter
from conscio.failure import FailureClass, FailureGovernor


def test_adapter_http_error_inheritance_and_status():
    err = AdapterHTTPError("endpoint: HTTP 429: Too Many Requests", status=429)
    assert isinstance(err, AdapterBadResponse)
    assert err.status == 429
    assert str(err) == "endpoint: HTTP 429: Too Many Requests"


def test_classify_adapter_http_error_statuses():
    # 429 -> RATE_LIMIT
    assert (
        FailureGovernor.classify(AdapterHTTPError("HTTP 429", status=429))
        is FailureClass.RATE_LIMIT
    )

    # 401, 403 -> PERMANENT
    assert (
        FailureGovernor.classify(AdapterHTTPError("HTTP 401", status=401))
        is FailureClass.PERMANENT
    )
    assert (
        FailureGovernor.classify(AdapterHTTPError("HTTP 403", status=403))
        is FailureClass.PERMANENT
    )

    # >= 500 -> PROVIDER_OUTAGE
    assert (
        FailureGovernor.classify(AdapterHTTPError("HTTP 500", status=500))
        is FailureClass.PROVIDER_OUTAGE
    )
    assert (
        FailureGovernor.classify(AdapterHTTPError("HTTP 503", status=503))
        is FailureClass.PROVIDER_OUTAGE
    )

    # Other 4xx -> MALFORMED_STREAM (preserves fallback to T3 for 400)
    assert (
        FailureGovernor.classify(AdapterHTTPError("HTTP 400", status=400))
        is FailureClass.MALFORMED_STREAM
    )
    assert (
        FailureGovernor.classify(AdapterHTTPError("HTTP 404", status=404))
        is FailureClass.MALFORMED_STREAM
    )


def test_post_json_raises_adapter_http_error_on_urllib_http_error():
    url = "https://api.example.com/v1/chat"
    http_err = urllib.error.HTTPError(
        url=url,
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=io.BytesIO(b"rate limited"),
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(AdapterHTTPError) as exc_info:
            _post_json(url, {"prompt": "hi"}, timeout=5.0)

    err = exc_info.value
    assert isinstance(err, AdapterBadResponse)
    assert err.status == 429
    assert str(err) == "https://api.example.com/v1/chat: HTTP 429: Too Many Requests"


def test_fallback_adapter_switches_provider_on_adapter_http_error():
    fallback = FallbackAdapter(base_url="http://localhost:1234/v1", models=["m1", "m2"])
    primary = MagicMock()
    primary.generate.side_effect = AdapterHTTPError("HTTP 500: Server Error", status=500)
    secondary = MagicMock()
    secondary.generate.return_value = InferenceResult(text='{"ok": true}')

    fallback._adapters = [primary, secondary]

    result = fallback.generate("prompt")
    assert result.text == '{"ok": true}'
    assert primary.generate.call_count == 1
    assert secondary.generate.call_count == 1


def test_fallback_multi_switches_provider_on_adapter_http_error():
    adapter1 = MagicMock()
    adapter1.generate.side_effect = AdapterHTTPError("HTTP 500: Server Error", status=500)

    adapter2 = MagicMock()
    adapter2.generate.return_value = InferenceResult(text='{"ok": true}')

    multi = MultiProviderFallbackAdapter(
        providers=[
            {"model": "m1", "base_url": "http://p1"},
            {"model": "m2", "base_url": "http://p2"},
        ],
        retry_per_provider=1,
        backoff_base=0.01,
    )
    multi._adapters = [adapter1, adapter2]

    result = multi.generate("prompt")
    assert result.text == '{"ok": true}'
    assert adapter1.generate.call_count == 1
    assert adapter2.generate.call_count == 1
