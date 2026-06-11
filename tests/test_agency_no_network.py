# tests/test_agency_no_network.py
"""A5: the pure agentic core must import and run a full PROPOSE->approve
cycle with zero network access and without pydantic/outlines installed."""
import json
import socket

import pytest


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted in pure-core test")
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def test_full_cycle_offline(no_network, tmp_path):
    from conscio.agency import (ActPipeline, ActStatus, CircuitBreaker,
                                MockAdapter, ActionLedger, Risk, ToolRegistry)

    registry = ToolRegistry()
    registry.register("echo", lambda text: text,
                      params={"text": {"type": "str", "required": True}},
                      risk=Risk.LOW, description="echo")
    ledger = ActionLedger(tmp_path / "conscio.db")
    pipeline = ActPipeline(
        adapter=MockAdapter(script=[json.dumps(
            {"tool": "echo", "args": {"text": "offline"},
             "rationale": "r", "expected_outcome": "e"})]),
        registry=registry, ledger=ledger,
        breaker=CircuitBreaker(ledger, type("B", (), {
            "emit": staticmethod(lambda **kw: None)})()))

    from conscio.context_manager import ConsciousnessState
    report = pipeline.act(ConsciousnessState(active_goals=["g"]))
    assert report.status is ActStatus.PROPOSED
    done = pipeline.approve(report.ledger_id)
    assert done.status is ActStatus.EXECUTED and done.result.output == "offline"


def test_agency_source_never_imports_optional_deps():
    """Guard: no agency module may IMPORT pydantic/outlines (import-line
    scan — docstrings may mention them; robust even if another test/plugin
    already imported them)."""
    import pathlib
    import re

    import conscio.agency
    pattern = re.compile(r"^\s*(import|from)\s+(pydantic|outlines)\b",
                         re.MULTILINE)
    src_dir = pathlib.Path(conscio.agency.__file__).parent
    for src in src_dir.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        assert not pattern.search(text), src.name
