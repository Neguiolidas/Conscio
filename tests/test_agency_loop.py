# tests/test_agency_loop.py
"""AutonomyLoop — engine.run(budget) L3 heartbeat with binding budget."""
from conscio.agency.act import ActReport, ActStatus
from conscio.agency.adapter import Meter
from conscio.agency.loop import ActBudget, AutonomyLoop, RunReport
from conscio.context_manager import ConsciousnessState
from conscio.dreaming import DreamRecommendation
from conscio.models import ContextMode, ModelInfo


class _FakePipeline:
    def __init__(self):
        self.autonomy_cap = 2
        self.caps_seen = []


class _FakeEngine:
    def __init__(self, *, lockdown_after=None, recommend_dream=False):
        self.state = ConsciousnessState(active_goals=["g"])
        self.model_info = ModelInfo("m", 131_000, ContextMode.COMPACT)
        self.session_tokens_used = None
        self.dream_recommended = DreamRecommendation(recommend_dream,
                                                     None, None)
        self.reflects = 0
        self.acts = 0
        self.dreams = 0
        self.lockdown_after = lockdown_after
        self.pipeline = _FakePipeline()

    def reflect(self, world_state=""):
        self.reflects += 1
        return {}

    def act(self):
        self.acts += 1
        self.pipeline.caps_seen.append(self.pipeline.autonomy_cap)
        if self.lockdown_after and self.acts >= self.lockdown_after:
            return ActReport(status=ActStatus.FAILED, lockdown=True)
        return ActReport(status=ActStatus.PROPOSED)

    def dream(self, dry_run=False):
        self.dreams += 1


def _run(engine, budget, meter=None):
    loop = AutonomyLoop(engine, engine.pipeline, meter or Meter())
    return loop.run(budget)


class TestBudgetStops:
    def test_stops_at_max_cycles(self):
        engine = _FakeEngine()
        report = _run(engine, ActBudget(max_cycles=3))
        assert report.cycles == 3
        assert report.stopped == "max_cycles"
        assert engine.reflects == 3 and engine.acts == 3

    def test_stops_at_max_llm_calls(self):
        engine = _FakeEngine()
        report = _run(engine, ActBudget(max_cycles=10, max_llm_calls=0))
        assert report.cycles == 0
        assert report.stopped == "max_llm_calls"

    def test_llm_calls_measured_as_delta(self):
        engine = _FakeEngine()
        meter = Meter()
        meter.calls = 99                      # pre-spent before run()
        report = _run(engine, ActBudget(max_cycles=1, max_llm_calls=5),
                      meter)
        assert report.cycles == 1             # delta 0 < 5: not exhausted
        assert report.llm_calls == 0

    def test_stops_at_max_tokens(self):
        engine = _FakeEngine()
        report = _run(engine, ActBudget(max_tokens=0))
        assert report.stopped == "max_tokens"
        assert report.tokens == 0

    def test_stops_at_max_wall_s(self):
        engine = _FakeEngine()
        report = _run(engine, ActBudget(max_wall_s=0.0))
        assert report.cycles == 0
        assert report.stopped == "max_wall_s"


class TestLockdownAndDream:
    def test_lockdown_stops_loop(self):
        engine = _FakeEngine(lockdown_after=2)
        report = _run(engine, ActBudget(max_cycles=10))
        assert report.cycles == 2
        assert report.stopped == "lockdown"

    def test_locked_status_stops_loop(self):
        engine = _FakeEngine()
        engine.act = lambda: ActReport(status=ActStatus.LOCKED)
        report = _run(engine, ActBudget(max_cycles=10))
        assert report.cycles == 1
        assert report.stopped == "lockdown"

    def test_dream_runs_when_recommended(self):
        engine = _FakeEngine(recommend_dream=True)
        _run(engine, ActBudget(max_cycles=2))
        assert engine.dreams == 2

    def test_no_dream_by_default(self):
        engine = _FakeEngine()
        _run(engine, ActBudget(max_cycles=2))
        assert engine.dreams == 0


class TestMetabolicGate:
    def test_critical_forces_propose_cap(self):
        engine = _FakeEngine()
        # tiny window -> any state injection is >= 70% usage = CRITICAL
        engine.model_info = ModelInfo("m", 10, ContextMode.MINIMAL)
        report = _run(engine, ActBudget(max_cycles=1))
        assert engine.pipeline.caps_seen == [1]
        assert engine.pipeline.autonomy_cap == 2      # restored after run
        assert report.cycles == 1

    def test_vital_keeps_cap(self):
        engine = _FakeEngine()
        _run(engine, ActBudget(max_cycles=1))
        assert engine.pipeline.caps_seen == [2]

    def test_fatigue_halves_cycle_budget(self):
        engine = _FakeEngine()
        # ~550 injected tokens vs a 1000-token window => ~55% = FATIGUE
        engine.model_info = ModelInfo("m", 1000, ContextMode.MINIMAL)
        engine.state.state_summary = "x" * 2200   # ~550 tokens injected
        report = _run(engine, ActBudget(max_cycles=8))
        assert report.cycles == 4                  # 8 // 2
        assert report.stopped == "max_cycles"


class TestPublicStateContract:
    def test_loop_uses_public_state_not_private(self):
        engine = _FakeEngine()
        assert not hasattr(engine, "_state")
        report = _run(engine, ActBudget(max_cycles=1))
        assert report.cycles == 1


class TestReportShape:
    def test_report_collects_act_reports(self):
        engine = _FakeEngine()
        report = _run(engine, ActBudget(max_cycles=2))
        assert isinstance(report, RunReport)
        assert len(report.reports) == 2
        assert all(isinstance(r, ActReport) for r in report.reports)
        assert report.wall_s >= 0.0
