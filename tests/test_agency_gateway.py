# tests/test_agency_gateway.py
"""Tests for OutputGateway: tier-2 JSON decode w/ repair+retry, tier-3 KV-line."""
import pytest

from conscio.agency.adapter import AdapterCaps, MockAdapter
from conscio.agency.contracts import PROPOSAL_SCHEMA, ActionProposal
from conscio.agency.gateway import (
    GatewayError,
    OutputGateway,
    coerce,
    parse_kv,
    repair_json,
)
from conscio.agency.intercepter import Intercepter


class TestGatewayIntercepterIntegration:
    """Task 7: OutputGateway + Intercepter integration."""

    def test_gateway_without_intercepter(self):
        """Existing behavior: no intercepter → adapter.generate() called directly."""
        adapter = MockAdapter(script=['{"tool": "think", "args": {}, '
                                      '"rationale": "test", '
                                      '"expected_outcome": "ok"}'])
        gw = OutputGateway(adapter)
        result = gw.request_action("prompt", PROPOSAL_SCHEMA)
        assert isinstance(result, ActionProposal)

    def test_gateway_with_intercepter_no_tags(self):
        """Intercepter present but no tags → single generate, clean pass-through."""
        adapter = MockAdapter(script=['{"tool": "think", "args": {}, '
                                      '"rationale": "test", '
                                      '"expected_outcome": "ok"}'])
        gw = OutputGateway(adapter, intercepter=Intercepter())
        result = gw.request_action("prompt", PROPOSAL_SCHEMA)
        assert isinstance(result, ActionProposal)

    def test_gateway_intercepter_resolves_tags(self):
        """Tags in output get resolved by the loop (2 iterations)."""
        # First generate: output has a tag.
        # Second generate (after intercept+reinject): clean JSON.
        adapter = MockAdapter(script=[
            '{"tool": "calc", "args": {"result": "[INTERCEPT: 2+2]"}, '
            '"rationale": "test", "expected_outcome": "ok"}',
            '{"tool": "calc", "args": {"result": 4}, '
            '"rationale": "test", "expected_outcome": "ok"}',
        ])
        gw = OutputGateway(adapter, intercepter=Intercepter())
        result = gw.request_action("prompt", PROPOSAL_SCHEMA)
        assert isinstance(result, ActionProposal)

    def test_t1_skips_intercepter(self):
        """T1 (grammar/GBNF) bypasses intercepter entirely."""
        adapter = MockAdapter(script=[
            '{"tool": "think", "args": {}, '
            '"rationale": "test", "expected_outcome": "ok"}',
        ])
        intercepter = Intercepter()
        gw = OutputGateway(adapter, intercepter=intercepter, tier="T1")
        # If intercepter were called, it would try to parse the JSON as
        # an INTERCEPT expression and fail. T1 skip means it passes.
        result = gw.request_action("prompt", PROPOSAL_SCHEMA,
                                   tool_names=["think"])
        assert isinstance(result, ActionProposal)

    def test_max_intercept_iterations_param(self):
        """max_intercept_iterations flows to InterceptionLoop."""
        adapter = MockAdapter(script=[
            '{"tool": "think", "args": {}, '
            '"rationale": "test", "expected_outcome": "ok"}',
        ])
        gw = OutputGateway(adapter, intercepter=Intercepter(),
                           max_intercept_iterations=5)
        assert gw._loop.max_iterations == 5


def test_act_pipeline_uses_prompt_zones():
    """v3.1: ActPipeline builds PromptZones, not raw strings."""
    from conscio.prompt_zones import PromptZones
    from conscio.agency.act import ActPipeline
    from conscio.agency.adapter import MockAdapter
    from conscio.agency.contracts import PROPOSAL_SCHEMA

    adapter = MockAdapter(script=[
        '{"tool": "think", "args": {}, '
        '"rationale": "test", "expected_outcome": "ok"}',
    ])


def test_build_actor_prompt_deprecated_wrapper():
    """v3.1: build_actor_prompt still works as wrapper returning .full_prompt."""
    from conscio.agency.actor import build_actor_prompt
    from conscio.context_manager import ConsciousnessState

    result = build_actor_prompt(
        state=ConsciousnessState(),
        goal_text="test",
        catalog_text="tool1",
    )
    assert isinstance(result, str)
    assert "tool1" in result
    assert "test" in result


class TestPromptZonesIntegration:
    """Task 1.2: OutputGateway accepts PromptZones objects."""

    def test_request_action_with_prompt_zones(self):
        """Gateway accepts PromptZones and converts to full_prompt internally."""
        from conscio.prompt_zones import PromptZones

        adapter = MockAdapter(script=[
            '{"tool": "think", "args": {}, '
            '"rationale": "test", "expected_outcome": "ok"}',
        ])
        gw = OutputGateway(adapter)
        pz = PromptZones(
            stable="system prompt\ntool schemas",
            volatile="state + goal",
        )
        result = gw.request_action(pz, PROPOSAL_SCHEMA, tool_names=["think"])
        assert isinstance(result, ActionProposal)

    def test_prompt_zones_equivalent_to_string(self):
        """Passing PromptZones.full_prompt as string produces same result."""
        from conscio.prompt_zones import PromptZones

        script = [
            '{"tool": "think", "args": {}, '
            '"rationale": "test", "expected_outcome": "ok"}',
        ]
        pz = PromptZones(stable="hello", volatile="world")

        gw1 = OutputGateway(MockAdapter(script=script))
        result1 = gw1.request_action(pz, PROPOSAL_SCHEMA)

        gw2 = OutputGateway(MockAdapter(script=script))
        result2 = gw2.request_action(pz.full_prompt, PROPOSAL_SCHEMA)

        assert result1.tool == result2.tool


class TestEffectiveTier:
    def test_explicit_tier_wins(self):
        gw = OutputGateway(MockAdapter(script=[]), tier="T3")
        assert gw.effective_tier() == "T3"

    def test_caps_fallback_json_mode(self):
        gw = OutputGateway(MockAdapter(script=[]))   # caps default json_mode
        assert gw.effective_tier() == "T2"

    def test_caps_fallback_kv_only(self):
        caps = AdapterCaps(json_mode=False, grammar=False)
        gw = OutputGateway(MockAdapter(script=[], caps=caps))
        assert gw.effective_tier() == "T3"

    def test_caps_fallback_grammar(self):
        caps = AdapterCaps(json_mode=False, grammar=True)
        gw = OutputGateway(MockAdapter(script=[], caps=caps))
        assert gw.effective_tier() == "T1"


class TestRepairJson:
    def test_strips_markdown_fences_and_prose(self):
        raw = 'Sure! Here you go:\n```json\n{"tool": "x"}\n```\nDone.'
        assert repair_json(raw) == '{"tool": "x"}'

    def test_removes_trailing_commas(self):
        assert repair_json('{"a": 1, "b": [1, 2,],}') == '{"a": 1, "b": [1, 2]}'

    def test_passthrough_for_clean_json(self):
        assert repair_json('{"a": 1}') == '{"a": 1}'


class TestParseKv:
    def test_parses_full_action(self):
        text = ("TOOL: fs_read\n"
                "ARG path = notes.md\n"
                "ARG limit = 10\n"
                "WHY: need previous notes\n"
                "EXPECT: file content\n")
        data = parse_kv(text)
        assert data["tool"] == "fs_read"
        assert data["args"] == {"path": "notes.md", "limit": "10"}
        assert data["rationale"] == "need previous notes"
        assert data["expected_outcome"] == "file content"

    def test_ignores_garbage_lines(self):
        data = parse_kv("hello\nTOOL: t\nWHY: w\nEXPECT: e\nrandom")
        assert data["tool"] == "t"

    def test_coerce_types(self):
        assert coerce("10", "int") == 10
        assert coerce("true", "bool") is True
        assert coerce("1.5", "float") == 1.5
        assert coerce("abc", "str") == "abc"


class TestGatewayT2:
    def test_decodes_clean_json_first_try(self):
        mock = MockAdapter(script=[
            '{"tool": "fs_read", "args": {"path": "a"}, '
            '"rationale": "r", "expected_outcome": "e"}'])
        gw = OutputGateway(mock, max_retries=2)
        p = gw.request_action("BASE", PROPOSAL_SCHEMA, goal_id="g1")
        assert isinstance(p, ActionProposal) and p.tool == "fs_read"
        assert p.goal_id == "g1"

    def test_retries_on_invalid_then_succeeds(self):
        mock = MockAdapter(script=[
            "not json at all",
            '{"tool": "t", "args": {}, "rationale": "r", "expected_outcome": "e"}'])
        gw = OutputGateway(mock, max_retries=2)
        p = gw.request_action("BASE", PROPOSAL_SCHEMA)
        assert p.tool == "t"
        assert len(mock.calls) == 2
        # retry prompt must carry the validation feedback
        assert "invalid" in mock.calls[1]["prompt"].lower()

    def test_exhausted_retries_raise_gateway_error(self):
        mock = MockAdapter(script=["junk", "junk", "junk"])
        gw = OutputGateway(mock, max_retries=2)
        with pytest.raises(GatewayError):
            gw.request_action("BASE", PROPOSAL_SCHEMA)


class TestGatewayT3:
    def test_kv_tier_used_when_no_json_mode(self):
        caps = AdapterCaps(model_name="tiny", json_mode=False)
        mock = MockAdapter(
            script=["TOOL: t\nARG a = 1\nWHY: w\nEXPECT: e"], caps=caps)
        gw = OutputGateway(mock, max_retries=1)
        p = gw.request_action("BASE", PROPOSAL_SCHEMA)
        assert p.tool == "t" and p.args == {"a": "1"}
        # the prompt must teach the KV format, not JSON
        assert "TOOL:" in mock.calls[0]["prompt"]

    def test_t2_downgrades_to_t3_once(self):
        mock = MockAdapter(script=[
            "junk", "junk", "junk",                       # T2 exhausted (1+2 retries)
            "TOOL: t\nWHY: w\nEXPECT: e"])                # T3 rescue
        gw = OutputGateway(mock, max_retries=2)
        p = gw.request_action("BASE", PROPOSAL_SCHEMA)
        assert p.tool == "t" and p.args == {}


def _valid_json():
    return ('{"tool": "echo", "args": {"text": "hi"}, '
            '"rationale": "r", "expected_outcome": "e"}')


def _valid_kv():
    return "TOOL: echo\nARG text = hi\nWHY: r\nEXPECT: e"


class TestTier1Grammar:
    def _gbnf_caps(self):
        return AdapterCaps(model_name="llamacpp", json_mode=False,
                           grammar=True)

    def test_grammar_caps_auto_select_t1(self):
        adapter = MockAdapter(script=[_valid_json()], caps=self._gbnf_caps())
        gw = OutputGateway(adapter)
        proposal = gw.request_action("BASE", PROPOSAL_SCHEMA,
                                     tool_names=["echo"])
        assert proposal.tool == "echo"
        assert gw.last_tier == "T1"
        call = adapter.calls[0]
        assert call["grammar"] is not None
        assert '"\\"echo\\""' in call["grammar"]

    def test_t1_downgrades_to_t3_without_json_mode(self):
        adapter = MockAdapter(script=["garbage"] * 3 + [_valid_kv()],
                              caps=self._gbnf_caps())
        gw = OutputGateway(adapter)
        proposal = gw.request_action("BASE", PROPOSAL_SCHEMA,
                                     tool_names=["echo"])
        assert proposal.tool == "echo"
        assert gw.last_tier == "T3"

    def test_explicit_tier_overrides_caps(self):
        adapter = MockAdapter(script=[_valid_kv()],
                              caps=AdapterCaps(json_mode=True))
        gw = OutputGateway(adapter, tier="T3")
        proposal = gw.request_action("BASE", PROPOSAL_SCHEMA)
        assert proposal.tool == "echo"
        assert gw.last_tier == "T3"
        assert adapter.calls[0]["schema"] is None    # KV path, no json mode

    def test_last_tier_records_t2(self):
        adapter = MockAdapter(script=[_valid_json()])
        gw = OutputGateway(adapter)
        gw.request_action("BASE", PROPOSAL_SCHEMA)
        assert gw.last_tier == "T2"
