# tests/test_agency_act.py
"""ActPipeline end-to-end with fakes: L1 PROPOSE, approve/reject,
A1 (no context leakage across 100 cycles) and A4 (breaker -> lockdown
persistence, reflect untouched)."""
import json
import sqlite3

import pytest

from conscio.agency.act import ActStatus, ActPipeline
from conscio.agency.adapter import (AdapterError, InferenceAdapter,
                                    AdapterCaps, MockAdapter)
from conscio.agency.breaker import CircuitBreaker
from conscio.agency.ledger import ActionLedger
from conscio.agency.tools import Risk, ToolRegistry
from conscio.context_manager import ConsciousnessState


class _FakeBus:
    def __init__(self):
        self.events = []

    def emit(self, **kw):
        self.events.append(kw)
        return 1


def _proposal_json(tool="echo", args=None):
    return json.dumps({"tool": tool, "args": args or {"text": "hi"},
                       "rationale": "r", "expected_outcome": "e"})


def _registry():
    reg = ToolRegistry()
    reg.register("echo", lambda text: text.upper(),
                 params={"text": {"type": "str", "required": True}},
                 risk=Risk.LOW, description="uppercase echo")
    return reg


def _pipeline(tmp_path, adapter, bus=None):
    ledger = ActionLedger(tmp_path / "conscio.db")
    bus = bus or _FakeBus()
    return ActPipeline(adapter=adapter, registry=_registry(), ledger=ledger,
                       breaker=CircuitBreaker(ledger, bus),
                       emit_fn=bus.emit), ledger, bus


def _state(goal="organize notes"):
    return ConsciousnessState(state_summary="s", active_goals=[goal],
                              coherence_note="epistemic")


class TestProposeFlow:
    def test_act_proposes_without_executing(self, tmp_path):
        pipeline, ledger, _ = _pipeline(
            tmp_path, MockAdapter(script=[_proposal_json()]))
        report = pipeline.act(_state())
        assert report.status is ActStatus.PROPOSED
        assert report.proposal.tool == "echo"
        assert ledger.get(report.ledger_id)["status"] == "proposed"
        assert report.result is None            # L1: nothing executed

    def test_approve_executes_and_updates_ledger(self, tmp_path):
        pipeline, ledger, _ = _pipeline(
            tmp_path, MockAdapter(script=[_proposal_json()]))
        report = pipeline.act(_state())
        executed = pipeline.approve(report.ledger_id)
        assert executed.status is ActStatus.EXECUTED
        assert executed.result.ok and executed.result.output == "HI"
        assert ledger.get(report.ledger_id)["status"] == "executed"

    def test_reject_marks_ledger(self, tmp_path):
        pipeline, ledger, _ = _pipeline(
            tmp_path, MockAdapter(script=[_proposal_json()]))
        report = pipeline.act(_state())
        pipeline.reject(report.ledger_id, reason="not now")
        assert ledger.get(report.ledger_id)["status"] == "rejected"

    def test_reject_only_touches_pending_proposals(self, tmp_path):
        pipeline, ledger, _ = _pipeline(
            tmp_path, MockAdapter(script=[_proposal_json()]))
        report = pipeline.act(_state())
        pipeline.approve(report.ledger_id)
        pipeline.reject(report.ledger_id, reason="too late")  # must be a no-op
        assert ledger.get(report.ledger_id)["status"] == "executed"

    def test_unknown_tool_fails_cycle(self, tmp_path):
        pipeline, ledger, _ = _pipeline(
            tmp_path, MockAdapter(script=[_proposal_json(tool="ghost")]))
        report = pipeline.act(_state())
        assert report.status is ActStatus.FAILED
        assert "unknown tool" in report.reason

    def test_no_goals_fails_cleanly(self, tmp_path):
        pipeline, _, _ = _pipeline(tmp_path, MockAdapter(script=[]))
        report = pipeline.act(ConsciousnessState(active_goals=[]))
        assert report.status is ActStatus.FAILED
        assert "no active goals" in report.reason

    def test_phases_emit_tool_call_events(self, tmp_path):
        pipeline, _, bus = _pipeline(
            tmp_path, MockAdapter(script=[_proposal_json()]))
        pipeline.act(_state())
        assert {e["type"] for e in bus.events} == {"tool_call"}
        assert {e["category"] for e in bus.events} == {"external"}
        assert all(isinstance(e["data"], dict) for e in bus.events)


class TestA1NoLeakage:
    def test_100_cycles_no_cross_contamination(self, tmp_path):
        script = [_proposal_json() for _ in range(100)]
        adapter = MockAdapter(script=script)
        pipeline, _, _ = _pipeline(tmp_path, adapter)
        for i in range(100):
            pipeline.act(_state(goal=f"GOAL_MARKER_{i}"))
        for i, call in enumerate(adapter.calls):
            assert f"GOAL_MARKER_{i}" in call["prompt"]
            if i > 0:
                assert f"GOAL_MARKER_{i - 1}" not in call["prompt"]


class _AlwaysFails(InferenceAdapter):
    def generate(self, prompt, **kw):
        raise AdapterError("backend down")

    def capabilities(self):
        return AdapterCaps()


class TestA4Breaker:
    def test_lockdown_after_max_retries_and_reflect_untouched(self, tmp_path):
        pipeline, _, bus = _pipeline(tmp_path, _AlwaysFails())
        state = _state(goal="stuck goal")
        for _ in range(3):                      # DEFAULT_MAX_RETRIES
            report = pipeline.act(state)
            assert report.status is ActStatus.FAILED
            state = ConsciousnessState(
                active_goals=["stuck goal"],
                action_lockdown=state.action_lockdown or report.lockdown)
        assert state.action_lockdown is True
        assert any("Intractable dissonance"
                   in e.get("data", {}).get("message", "")
                   for e in bus.events)
        locked = pipeline.act(state)
        assert locked.status is ActStatus.LOCKED   # early abort, no LLM call

    def test_lockdown_survives_save_load_roundtrip(self, tmp_path):
        from conscio.context_manager import ContextManager
        manager = ContextManager("glm-5.1", storage_path=tmp_path)
        manager.save_state(ConsciousnessState(action_lockdown=True))
        assert manager.load_state().action_lockdown is True


class TestEngineIntegration:
    def test_attach_act_approve_smoke(self, tmp_path):
        """Full engine wiring with MockAdapter and isolated tmp dirs.

        F2: the same adapter serves the skeptic audit, so the script
        carries the checklist answer after the proposal.
        """
        from conscio import ConsciousnessEngine
        with ConsciousnessEngine(model_name="glm-5.1",
                                 storage_path=tmp_path) as engine:
            engine.attach_adapter(
                MockAdapter(script=[
                    _proposal_json(tool="fs_write",
                                   args={"path": "out.md", "content": "hi"}),
                    "A1: NO\nA2: NO\nA3: YES",      # skeptic checklist PASS
                ]),
                sandbox_root=tmp_path / "sb")
            state = _state(goal="write a note")
            report = engine.act(state)
            assert report.status is ActStatus.PROPOSED
            done = engine.approve(report.ledger_id)
            assert done.status is ActStatus.EXECUTED
            assert (tmp_path / "sb" / "out.md").read_text() == "hi"

    def test_close_closes_action_ledger(self, tmp_path):
        from conscio import ConsciousnessEngine
        with ConsciousnessEngine(model_name="glm-5.1",
                                 storage_path=tmp_path) as engine:
            pipeline = engine.attach_adapter(MockAdapter(script=[]),
                                             sandbox_root=tmp_path / "sb")
        with pytest.raises(sqlite3.ProgrammingError):
            pipeline.ledger.get(1)

    def test_act_without_adapter_fails_cleanly(self, tmp_path):
        from conscio import ConsciousnessEngine
        with ConsciousnessEngine(model_name="glm-5.1",
                                 storage_path=tmp_path) as engine:
            report = engine.act(_state())
            assert report.status is ActStatus.FAILED
            assert "no adapter" in report.reason
