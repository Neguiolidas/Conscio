# tests/test_agency_skills.py
"""SkillLibrary — procedural memory as data (spec v1.1: distill from the
ActionLedger, tier-aware few-shot serving, outcome settling)."""
import json
from types import SimpleNamespace

import pytest

from conscio.agency.act import goal_fingerprint
from conscio.agency.ledger import ActionLedger
from conscio.agency.skills import SkillLibrary

GOAL = "Investigate: anomaly in sandbox notes"
OTHER_GOAL = "Maintenance: prune stale entities"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "conscio.db"


@pytest.fixture
def ledger(db):
    led = ActionLedger(db)
    yield led
    led.close()


@pytest.fixture
def lib(db):
    library = SkillLibrary(db)
    yield library
    library.close()


def _success(ledger, goal=GOAL, tool="fs_read", args=None, rationale="why"):
    return ledger.record(
        goal_fp=goal_fingerprint(goal), goal_text=goal, tool=tool,
        args_json=json.dumps(args or {"path": "notes.md"}),
        rationale=rationale, tier="T2", status="executed", ok=True)


class TestDistill:
    def test_creates_skill_from_successful_actions(self, ledger, lib):
        _success(ledger, tool="fs_read", rationale="inspect")
        _success(ledger, tool="memory_note", args={"text": "x"},
                 rationale="record")
        assert lib.distill(ledger) == 1
        [skill] = lib.all()
        assert json.loads(skill["tool_seq"]) == ["fs_read", "memory_note"]
        assert skill["goal_text"] == GOAL
        assert skill["successes"] == 1 and skill["failures"] == 0
        steps = json.loads(skill["plan_template"])
        assert steps[0]["tool"] == "fs_read"
        assert steps[0]["args"] == {"path": "notes.md"}
        assert steps[0]["rationale"] == "inspect"

    def test_groups_by_goal(self, ledger, lib):
        _success(ledger, goal=GOAL)
        _success(ledger, goal=OTHER_GOAL, tool="goal_update",
                 args={"action": "complete", "goal_id": "g"})
        assert lib.distill(ledger) == 2
        assert lib.count() == 2

    def test_same_sequence_increments_successes(self, ledger, lib):
        _success(ledger)
        lib.distill(ledger)
        _success(ledger)
        assert lib.distill(ledger) == 1
        [skill] = lib.all()
        assert skill["successes"] == 2

    def test_watermark_makes_distill_idempotent(self, ledger, lib):
        _success(ledger)
        assert lib.distill(ledger) == 1
        assert lib.distill(ledger) == 0          # nothing new: A7
        [skill] = lib.all()
        assert skill["successes"] == 1

    def test_dry_run_counts_without_writing(self, ledger, lib):
        _success(ledger)
        assert lib.distill(ledger, dry_run=True) == 1
        assert lib.count() == 0                  # nothing persisted
        assert lib.distill(ledger) == 1          # watermark did not move

    def test_caps_at_five_most_recent_steps(self, ledger, lib):
        for i in range(7):
            _success(ledger, tool=f"t{i}")
        lib.distill(ledger)
        [skill] = lib.all()
        assert json.loads(skill["tool_seq"]) == ["t2", "t3", "t4", "t5", "t6"]

    def test_failed_rows_never_distill(self, ledger, lib):
        ledger.record(goal_fp=goal_fingerprint(GOAL), goal_text=GOAL,
                      tool="t", args_json="{}", rationale="", tier="T2",
                      status="failed", ok=False)
        assert lib.distill(ledger) == 0


class TestFewShot:
    def _seed(self, ledger, lib, goal=GOAL):
        _success(ledger, goal=goal)
        lib.distill(ledger)

    def test_exact_goal_match_serves_exemplar(self, ledger, lib):
        self._seed(ledger, lib)
        shots = lib.few_shot(GOAL, tier="T2")
        assert len(shots) == 1
        assert "Past successful plan" in shots[0]
        assert "fs_read" in shots[0]

    def test_serving_increments_uses(self, ledger, lib):
        self._seed(ledger, lib)
        lib.few_shot(GOAL, tier="T2")
        [skill] = lib.all()
        assert skill["uses"] == 1

    def test_similar_goal_matches_lexically(self, ledger, lib):
        self._seed(ledger, lib)
        shots = lib.few_shot("Investigate: new anomaly in notes", tier="T2")
        assert len(shots) == 1

    def test_unrelated_goal_serves_nothing(self, ledger, lib):
        self._seed(ledger, lib)
        assert lib.few_shot("Evolve: rewrite scheduler core", tier="T2") == []

    def test_low_success_rate_is_never_taught(self, ledger, lib):
        self._seed(ledger, lib)
        lib.few_shot(GOAL, tier="T2")
        lib.settle(SimpleNamespace(status="failed"))   # rate -> 0.5 ok
        lib.few_shot(GOAL, tier="T2")
        lib.settle(SimpleNamespace(status="failed"))   # rate -> 1/3 < 0.5
        assert lib.few_shot(GOAL, tier="T2") == []     # A12

    def test_serves_at_most_two(self, ledger, lib):
        for i in range(3):
            goal = f"{GOAL} variant {i}"
            _success(ledger, goal=goal)
        lib.distill(ledger)
        assert len(lib.few_shot(GOAL + " variant", tier="T2")) == 2

    def test_t3_renders_kv_lines(self, ledger, lib):
        self._seed(ledger, lib)
        [shot] = lib.few_shot(GOAL, tier="T3")
        assert "TOOL: fs_read" in shot
        assert "ARG path = notes.md" in shot
        assert "WHY: why" in shot

    def test_t2_renders_json_steps(self, ledger, lib):
        self._seed(ledger, lib)
        [shot] = lib.few_shot(GOAL, tier="T2")
        step_line = shot.splitlines()[1]
        data = json.loads(step_line)
        assert data["tool"] == "fs_read"
        assert data["args"] == {"path": "notes.md"}


class TestSettle:
    def _serve(self, ledger, lib):
        _success(ledger)
        lib.distill(ledger)
        lib.few_shot(GOAL, tier="T2")

    def test_executed_outcome_rewards_served_skill(self, ledger, lib):
        self._serve(ledger, lib)
        lib.settle(SimpleNamespace(status="executed"))
        [skill] = lib.all()
        assert skill["successes"] == 2

    def test_failed_outcome_penalizes_served_skill(self, ledger, lib):
        self._serve(ledger, lib)
        lib.settle(SimpleNamespace(status="failed"))
        [skill] = lib.all()
        assert skill["failures"] == 1

    def test_proposed_and_rejected_discard_without_scoring(self, ledger,
                                                           lib):
        _success(ledger)
        lib.distill(ledger)
        for status in ("proposed", "rejected", "locked"):
            lib.few_shot(GOAL, tier="T2")
            lib.settle(SimpleNamespace(status=status))
        [skill] = lib.all()
        assert skill["successes"] == 1 and skill["failures"] == 0

    def test_settle_is_single_shot(self, ledger, lib):
        self._serve(ledger, lib)
        lib.settle(SimpleNamespace(status="executed"))
        lib.settle(SimpleNamespace(status="executed"))   # slot consumed
        [skill] = lib.all()
        assert skill["successes"] == 2

    def test_settle_without_serve_is_noop(self, lib):
        lib.settle(SimpleNamespace(status="executed"))   # must not raise

    def test_settle_accepts_act_report_enum_status(self, ledger, lib):
        from conscio.agency.act import ActReport, ActStatus
        self._serve(ledger, lib)
        lib.settle(ActReport(status=ActStatus.EXECUTED))
        [skill] = lib.all()
        assert skill["successes"] == 2
