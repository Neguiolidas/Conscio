# tests/test_loop_failure_brake.py
"""v1.5.1 (#8): AutonomyLoop aggregate failure-rate brake.

The per-goal CircuitBreaker only trips on a SINGLE goal's consecutive failures.
Broad flailing across many distinct goals/tools (the field failure: 145 calls /
62.8% success) never trips it, so an awake loop burns budget with no benefit.
The aggregate brake stops the heartbeat when the failure rate over a window of
attempts crosses a threshold. Tested in isolation from the act pipeline via fakes
so the per-goal breaker can't contaminate the signal.
"""
from conscio.agency.act import ActReport, ActStatus
from conscio.agency.loop import ActBudget, AutonomyLoop


class _FakeMeter:
    calls = 0
    tokens = 0


class _FakePipeline:
    autonomy_cap = 3


class _FakeState:
    action_lockdown = False

    def total_tokens_approx(self):
        return 0


class _FakeModelInfo:
    context_window = 131000


class _FakeDream:
    recommended = False


class _FakeBus:
    def __init__(self):
        self.emitted = []

    def emit(self, **kwargs):
        self.emitted.append(kwargs)


class _FakeEngine:
    """Replays a fixed sequence of ActStatus values through act()."""

    def __init__(self, statuses):
        self._statuses = list(statuses)
        self._i = 0
        self.state = _FakeState()
        self.model_info = _FakeModelInfo()
        self.dream_recommended = _FakeDream()
        self.event_bus = _FakeBus()
        self.reflections = 0

    def reflect(self, world_state=""):
        self.reflections += 1

    def act(self):
        st = self._statuses[self._i]
        self._i += 1
        return ActReport(status=st)


def _loop(statuses):
    eng = _FakeEngine(statuses)
    return eng, AutonomyLoop(eng, _FakePipeline(), _FakeMeter())


def test_brake_trips_when_all_cycles_fail():
    eng, loop = _loop([ActStatus.FAILED] * 20)
    report = loop.run(ActBudget(max_cycles=20, max_wall_s=120.0,
                                max_failure_rate=0.5, min_attempts=4))
    assert report.stopped == "failure_rate"
    assert report.cycles == 4          # tripped at the min-attempts window
    assert report.failures == 4


def test_brake_counts_rejected_as_failure():
    # skeptic_fail (the field loop) surfaces as REJECTED, not FAILED.
    eng, loop = _loop([ActStatus.REJECTED] * 20)
    report = loop.run(ActBudget(max_cycles=20, max_wall_s=120.0,
                                max_failure_rate=0.5, min_attempts=4))
    assert report.stopped == "failure_rate"


def test_brake_does_not_trip_when_all_succeed():
    eng, loop = _loop([ActStatus.EXECUTED] * 5)
    report = loop.run(ActBudget(max_cycles=5, max_wall_s=120.0,
                                max_failure_rate=0.5, min_attempts=4))
    assert report.stopped == "max_cycles"
    assert report.failures == 0


def test_brake_trips_at_exact_threshold_not_below():
    # Alternating FAIL/EXEC -> 2 fails / 4 cycles = 0.5, which is >= 0.5 -> trip.
    seq = [ActStatus.FAILED, ActStatus.EXECUTED] * 10
    eng, loop = _loop(seq)
    report = loop.run(ActBudget(max_cycles=20, max_wall_s=120.0,
                                max_failure_rate=0.5, min_attempts=4))
    assert report.stopped == "failure_rate"
    assert report.cycles == 4
    assert report.failures == 2


def test_brake_emits_surfacing_event_on_trip():
    eng, loop = _loop([ActStatus.FAILED] * 20)
    loop.run(ActBudget(max_cycles=20, max_wall_s=120.0,
                       max_failure_rate=0.5, min_attempts=4))
    assert any("failure" in str(e.get("data", "")).lower()
               for e in eng.event_bus.emitted)
