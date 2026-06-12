# tests/test_agency_arbiter.py
"""GoalArbiter — priority x dissonance alignment x quarantine (5.11)."""
from conscio.agency.act import goal_fingerprint
from conscio.agency.loop import GoalArbiter
from conscio.context_manager import ConsciousnessState


class _FakeBreaker:
    def __init__(self, quarantined=()):
        self.q = {goal_fingerprint(g) for g in quarantined}
        self.reviewed = 0

    def review_quarantine(self):
        self.reviewed += 1
        return []

    def is_quarantined(self, fp):
        return fp in self.q


def _state(goals, note=""):
    return ConsciousnessState(active_goals=list(goals), coherence_note=note)


class TestChoose:
    def test_first_goal_wins_without_dissonance(self):
        arbiter = GoalArbiter(_FakeBreaker())
        state = _state(["alpha task", "beta task"])
        assert arbiter.choose(state) == "alpha task"

    def test_dissonance_alignment_beats_priority_order(self):
        arbiter = GoalArbiter(_FakeBreaker())
        state = _state(["organize files", "verify the anomaly"],
                       note="epistemic")
        assert arbiter.choose(state) == "verify the anomaly"

    def test_quarantined_goals_skipped(self):
        arbiter = GoalArbiter(_FakeBreaker(quarantined=["alpha task"]))
        state = _state(["alpha task", "beta task"])
        assert arbiter.choose(state) == "beta task"

    def test_all_quarantined_returns_none(self):
        arbiter = GoalArbiter(_FakeBreaker(quarantined=["a", "b"]))
        assert arbiter.choose(_state(["a", "b"])) is None

    def test_empty_goals_returns_none(self):
        arbiter = GoalArbiter(_FakeBreaker())
        assert arbiter.choose(_state([])) is None

    def test_reviews_quarantine_each_choice(self):
        breaker = _FakeBreaker()
        arbiter = GoalArbiter(breaker)
        arbiter.choose(_state(["g"]))
        assert breaker.reviewed == 1

    def test_priority_dominates_when_both_aligned(self):
        arbiter = GoalArbiter(_FakeBreaker())
        state = _state(["verify the logs", "verify the anomaly"],
                       note="epistemic")
        assert arbiter.choose(state) == "verify the logs"

    def test_unknown_dissonance_dimension_is_neutral(self):
        arbiter = GoalArbiter(_FakeBreaker())
        state = _state(["organize files", "verify the anomaly"],
                       note="unmapped-dimension")
        assert arbiter.choose(state) == "organize files"
