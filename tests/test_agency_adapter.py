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

    def test_script_accepts_callables_reacting_to_prompt(self):
        mock = MockAdapter(script=[lambda p: f"saw:{p}", "static"])
        assert mock.generate("ab").text == "saw:ab"
        assert mock.generate("cd").text == "static"

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


class TestMeteredAdapter:
    def test_counts_calls_and_tokens(self):
        from conscio.agency.adapter import Meter, MeteredAdapter
        meter = Meter()
        inner = MockAdapter(script=["hello world!"])
        metered = MeteredAdapter(inner, meter)
        result = metered.generate("a prompt of words")
        assert result.text == "hello world!"
        assert meter.calls == 1
        assert meter.tokens == result.tokens_in + result.tokens_out
        assert len(meter.latencies_ms) == 1

    def test_failed_call_still_debits_budget(self):
        from conscio.agency.adapter import Meter, MeteredAdapter
        meter = Meter()
        metered = MeteredAdapter(MockAdapter(script=[]), meter)
        with pytest.raises(AdapterError):
            metered.generate("p")
        assert meter.calls == 1 and meter.tokens == 0

    def test_capabilities_and_name_pass_through(self):
        from conscio.agency.adapter import Meter, MeteredAdapter
        inner = MockAdapter(script=[], caps=AdapterCaps(model_name="tiny"))
        metered = MeteredAdapter(inner, Meter())
        assert metered.capabilities().model_name == "tiny"
        assert metered.wrapped_name == "MockAdapter"
        assert isinstance(metered, InferenceAdapter)

    def test_kwargs_forwarded_to_inner(self):
        from conscio.agency.adapter import Meter, MeteredAdapter
        inner = MockAdapter(script=["x"])
        MeteredAdapter(inner, Meter()).generate(
            "p", grammar="root ::= ws", max_tokens=9, temperature=0.0,
            stop=["\n"])
        call = inner.calls[0]
        assert call["grammar"] == "root ::= ws"
        assert call["max_tokens"] == 9
        assert call["stop"] == ["\n"]
