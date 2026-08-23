# tests/test_agency_act.py
"""ActPipeline end-to-end with fakes: L1 PROPOSE, approve/reject,
A1 (no context leakage across 100 cycles) and A4 (breaker -> lockdown
persistence, reflect untouched)."""
import json
import sqlite3

import pytest

from conscio.agency.act import ActPipeline, ActStatus
from conscio.agency.adapter import (
    AdapterCaps,
    AdapterConnectionError,
    InferenceAdapter,
    MockAdapter,
)
from conscio.agency.breaker import CircuitBreaker
from conscio.agency.fingerprint import goal_fingerprint
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

    def test_act_records_goal_text_in_ledger(self, tmp_path):
        pipeline, ledger, _ = _pipeline(
            tmp_path, MockAdapter(script=[_proposal_json()]))
        report = pipeline.act(_state(goal="organize notes"))
        assert ledger.get(report.ledger_id)["goal_text"] == "organize notes"

    def test_fail_path_records_goal_text(self, tmp_path):
        pipeline, ledger, _ = _pipeline(
            tmp_path, MockAdapter(script=[_proposal_json(tool="ghost")]))
        report = pipeline.act(_state(goal="organize notes"))
        assert ledger.get(report.ledger_id)["goal_text"] == "organize notes"

    def test_unknown_tool_fails_cycle(self, tmp_path):
        pipeline, _ledger, _ = _pipeline(
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


class _ProseOnly(InferenceAdapter):
    """Model replies but never in the JSON proposal contract — decode failure
    (infra=False). A goal stuck here IS intractable: the model is up but
    cannot hand over a valid action, so the breaker still collapses it."""
    def generate(self, prompt, **kw):
        from conscio.agency.adapter import InferenceResult
        return InferenceResult(
            text="The answer is 42 because models prefer round numbers.",
            tokens_in=10, tokens_out=10)

    def capabilities(self):
        return AdapterCaps()


class TestA4Breaker:
    def test_lockdown_after_max_retries_and_reflect_untouched(self, tmp_path):
        pipeline, _, bus = _pipeline(tmp_path, _ProseOnly())
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


class TestDoubleApprove:
    """A6: approve() claims the row atomically (proposed -> executing);
    a repeated or concurrent approve() loses the claim and never
    re-dispatches the tool."""

    def test_double_approve_executes_once(self, tmp_path):
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
            report = engine.act(_state(goal="write a note"))
            assert report.status is ActStatus.PROPOSED
            first = engine.approve(report.ledger_id)
            second = engine.approve(report.ledger_id)
            assert first.status is ActStatus.EXECUTED
            assert second.status is ActStatus.FAILED
            assert "already handled" in (second.reason or "")
            assert (tmp_path / "sb" / "out.md").read_text() == "hi"


class TestTokensReachTheLedger:
    """What an autonomous action cost, on the row that records the action.

    Field report (v3.9.4, live daemon): 34 of 34 action rows carried
    tokens_in=0 and tokens_out=0. ActionLedger.record has taken both since
    v2.0.1 and no caller ever passed them, so the only per-action cost record
    the daemon keeps was always zero.
    """

    def _row(self, ledger, row_id):
        return ledger.get(row_id)

    def test_a_proposed_action_records_what_the_decode_cost(self, tmp_path):
        pipeline, ledger, _ = _pipeline(
            tmp_path, MockAdapter(script=[_proposal_json(),
                                          "A1: NO\nA2: NO\nA3: YES"]))
        report = pipeline.act(_state())
        row = ledger.get(report.ledger_id)
        assert row["tokens_in"] > 0
        assert row["tokens_out"] > 0

    def test_the_count_covers_every_call_the_ladder_made(self, tmp_path):
        # One proposal can take several calls. The action cost is all of them,
        # not the cost of the call that finally decoded.
        first_try = MockAdapter(script=[_proposal_json(),
                                        "A1: NO\nA2: NO\nA3: YES"])
        pipe_one, led_one, _ = _pipeline(tmp_path, first_try)
        cheap = led_one.get(pipe_one.act(_state()).ledger_id)

        retried = MockAdapter(script=["not json at all",      # T2 retries
                                      _proposal_json(),
                                      "A1: NO\nA2: NO\nA3: YES"])
        (tmp_path / "b").mkdir()
        pipe_two, led_two, _ = _pipeline(tmp_path / "b", retried)
        report = pipe_two.act(_state())
        assert report.status is ActStatus.PROPOSED     # the retry succeeded
        dear = led_two.get(report.ledger_id)

        assert dear["tokens_in"] > cheap["tokens_in"]
        assert dear["tokens_out"] > cheap["tokens_out"]

    def test_a_gateway_that_reports_nothing_records_zero(self, tmp_path):
        # Not every adapter returns usage; the row must degrade to 0, not crash.
        class _NoUsage(MockAdapter):
            def generate(self, prompt, **kw):
                result = super().generate(prompt, **kw)
                result.tokens_in = 0
                result.tokens_out = 0
                return result

        pipeline, ledger, _ = _pipeline(
            tmp_path, _NoUsage(script=[_proposal_json(),
                                       "A1: NO\nA2: NO\nA3: YES"]))
        row = ledger.get(pipeline.act(_state()).ledger_id)
        assert row["tokens_in"] == 0 and row["tokens_out"] == 0


class _InfraFails(InferenceAdapter):
    """Adapter whose endpoint is unreachable — infra, not a goal problem."""
    def generate(self, prompt, **kw):
        raise AdapterConnectionError("Connection refused: no route to host")
    def capabilities(self):
        return AdapterCaps()


class TestInfraFailuresDoNotTripleTheGoal:
    """A goal failing because the model endpoint is down is NOT 'intractable'.

    Field report (conscio 4.1.0, deepseek-v4-flash daemon): `started 4+
    days at 90-100% CPU`, `arbiter returned None — quarantined=[...]`, 145k
    log lines. The lone executable `host health check` goal was being
    quarantined by the breaker for `Connection refused` (infra), leaving
    zero actable goals and a 5-minute retry loop. A dead endpoint is an
    environment problem; the breaker must not collapse the goal for it.
    """

    def test_infra_failure_never_quarantines_the_goal(self, tmp_path):
        pipeline, _, bus = _pipeline(tmp_path, _InfraFails())
        state = _state(goal="Maintenance: host health check")
        fp = goal_fingerprint("Maintenance: host health check")
        for _ in range(6):                       # >> DEFAULT_MAX_RETRIES
            report = pipeline.act(state)
            assert report.status is ActStatus.FAILED
        assert not pipeline.breaker.is_quarantined(fp)
        assert not any("Intractable dissonance"
                       in e.get("data", {}).get("message", "")
                       for e in bus.events)
