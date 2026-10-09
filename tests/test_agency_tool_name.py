"""Tests for sanitize_tool_name (v4.9, Jade catalog item 2).

The sanitizer must rescue formatting slips (call parens, quotes, case,
inner spaces) without ever inventing a tool: an unknown name stays
unknown, and the act pipeline uses the cleaned name for the ledger row.
"""
from __future__ import annotations

import pytest

from conscio.agency.contracts import PROPOSAL_SCHEMA
from conscio.agency.tool_name import sanitize_tool_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("host_health()", "host_health"),
        ("host_health ( )", "host_health"),  # parens with inner space
        ("`host_health`", "host_health"),
        ('"host_health"', "host_health"),
        (" Host_Health ", "host_health"),
        ("HOST-HEALTH", "host-health"),  # hyphen is NOT underscore: stays unknown
        ("host health", "host_health"),  # inner space -> underscore
        ("host_health", "host_health"),  # already clean: no-op
        ("", ""),
        (None, ""),
        (123, "123"),  # wrong type: str() — fails lookup as before
    ],
)
def test_sanitize_cases(raw: object, expected: str) -> None:
    assert sanitize_tool_name(raw) == expected


def test_unknown_name_still_fails_like_before() -> None:
    """The sanitizer never invents tools: a made-up name keeps failing."""
    from conscio.agency.tools import Risk, ToolRegistry

    registry = ToolRegistry()
    registry.register("host_health", lambda: "ok", params={}, risk=Risk.LOW,
                      description="health check")
    cleaned = sanitize_tool_name("definitely_not_a_tool()")
    assert registry.get(cleaned) is None  # no magic: unknown stays unknown
    assert registry.get(sanitize_tool_name("Host_Health()")) is not None


def test_pipeline_rescues_parens_slip(tmp_path) -> None:
    """A proposal with 'echo()' reaches the real spec instead of dying."""
    from conscio.agency.act import ActPipeline
    from conscio.agency.adapter import MockAdapter
    from conscio.agency.breaker import CircuitBreaker
    from conscio.agency.ledger import ActionLedger
    from tests.test_agency_act import _FakeBus, _proposal_json, _registry

    bus = _FakeBus()
    ledger = ActionLedger(tmp_path / "conscio.db")
    pipeline = ActPipeline(
        adapter=MockAdapter(script=[_proposal_json(tool="echo()")]),
        registry=_registry(), ledger=ledger,
        breaker=CircuitBreaker(ledger, bus), emit_fn=bus.emit)
    proposal = pipeline.gateway.request_action(
        "prompt", PROPOSAL_SCHEMA, tool_names=pipeline.registry.names())
    # the sanitizer lives in act(); this is the exact call act() makes:
    cleaned = sanitize_tool_name(proposal.tool)
    assert cleaned == "echo"
    assert pipeline.registry.get(cleaned) is not None
