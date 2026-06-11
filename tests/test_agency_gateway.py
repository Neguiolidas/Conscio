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
