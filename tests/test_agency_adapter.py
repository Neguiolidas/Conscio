# tests/test_agency_adapter.py
"""Tests for the InferenceAdapter interface and the scriptable MockAdapter."""
import pytest

from conscio.agency.adapter import (
    AdapterCaps,
    AdapterError,
    InferenceAdapter,
    InferenceResult,
    MockAdapter,
)


class TestMockAdapter:
    def test_returns_scripted_responses_in_order(self):
        mock = MockAdapter(script=['{"a": 1}', '{"b": 2}'])
        assert mock.generate("first prompt").text == '{"a": 1}'
        assert mock.generate("second prompt").text == '{"b": 2}'

    def test_records_every_call_with_prompt_and_kwargs(self):
        mock = MockAdapter(script=["x"])
        mock.generate("hello", max_tokens=99, temperature=0.0)
        assert len(mock.calls) == 1
        assert mock.calls[0]["prompt"] == "hello"
        assert mock.calls[0]["max_tokens"] == 99

    def test_exhausted_script_raises_adapter_error(self):
        mock = MockAdapter(script=[])
        with pytest.raises(AdapterError):
            mock.generate("anything")

    def test_capabilities_default_to_json_mode(self):
        caps = MockAdapter(script=[]).capabilities()
        assert isinstance(caps, AdapterCaps)
        assert caps.json_mode is True

    def test_caps_are_overridable(self):
        caps = AdapterCaps(model_name="tiny-4b", json_mode=False, grammar=False)
        mock = MockAdapter(script=[], caps=caps)
        assert mock.capabilities().json_mode is False

    def test_is_an_inference_adapter(self):
        assert isinstance(MockAdapter(script=[]), InferenceAdapter)

    def test_result_carries_token_counts(self):
        r = InferenceResult(text="t", tokens_in=10, tokens_out=5, latency_ms=3)
        assert (r.tokens_in, r.tokens_out) == (10, 5)
