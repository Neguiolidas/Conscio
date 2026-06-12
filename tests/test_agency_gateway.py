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
