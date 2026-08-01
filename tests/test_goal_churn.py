# tests/test_goal_churn.py
"""Goal deduplication: a drive re-proposing the same work must not queue it twice,
and cancelling that work must actually stop it.

Field report (v3.9.3, live daemon): maintenance goals piled up cycle after cycle
and cancelling them changed nothing — the next reflect() put them straight back.
Two defects, both covered here: the goal's identity was its description, which
embeds a live reading that shifts between cycles; and dedup ignored cancelled
goals entirely.
"""
from datetime import datetime, timedelta

from conscio.goal_generator import Drive, GoalGenerator


def _active(g: GoalGenerator) -> list:
    return [x for x in g._goals if x.status == "active"]


class TestIdentityIsTheCheck:
    """A maintenance goal is the same goal when it runs the same check."""

    def test_shifting_target_does_not_mint_a_second_goal(self, tmp_path):
        g = GoalGenerator(tmp_path)
        g.generate_from_maintenance("prune_stale", "23 stale entities: a, b, c")
        # One more entity goes stale: same check, new wording.
        g.generate_from_maintenance("prune_stale", "24 stale entities: a, b, d")
        assert len(_active(g)) == 1

    def test_a_different_check_is_a_different_goal(self, tmp_path):
        g = GoalGenerator(tmp_path)
        g.generate_from_maintenance("prune_stale", "entities")
        g.generate_from_maintenance("self_prompt", "entities")
        assert len(_active(g)) == 2

    def test_non_maintenance_goals_still_key_on_description(self, tmp_path):
        g = GoalGenerator(tmp_path)
        g.generate_from_curiosity("why did X spike")
        g.generate_from_curiosity("why did X spike")   # same question
        g.generate_from_curiosity("why did Y spike")   # different question
        assert len(_active(g)) == 2


class TestCancelSticks:
    """Cancelling has to survive the next reflect()."""

    def test_cancelled_check_is_not_regenerated(self, tmp_path):
        g = GoalGenerator(tmp_path)
        goal = g.generate_from_maintenance("prune_stale", "23 stale entities")
        assert g.cancel_goal(goal.id) is True

        g.generate_from_maintenance("prune_stale", "24 stale entities")
        assert _active(g) == []

    def test_cancellation_survives_a_daemon_restart(self, tmp_path):
        g = GoalGenerator(tmp_path)
        goal = g.generate_from_maintenance("prune_stale", "stale entities")
        g.cancel_goal(goal.id)

        reloaded = GoalGenerator(tmp_path)                # fresh process
        reloaded.generate_from_maintenance("prune_stale", "more stale entities")
        assert _active(reloaded) == []

    def test_the_silence_expires(self, tmp_path):
        """`goal_update cancel` is a tool the agent can call on itself, so the
        tombstone has a clock: a day later the check may propose itself again."""
        g = GoalGenerator(tmp_path)
        goal = g.generate_from_maintenance("prune_stale", "stale entities")
        g.cancel_goal(goal.id)
        goal.cancelled_at = (
            datetime.now() - timedelta(hours=g.CANCEL_COOLDOWN_HOURS + 1)
        ).isoformat()

        g.generate_from_maintenance("prune_stale", "stale entities")
        assert len(_active(g)) == 1

    def test_a_cancel_from_before_the_field_existed_does_not_suppress(self, tmp_path):
        """Goals cancelled by an older version carry no timestamp. Treating an
        undated tombstone as fresh would silence those checks forever."""
        g = GoalGenerator(tmp_path)
        goal = g.generate_from_maintenance("prune_stale", "stale entities")
        g.cancel_goal(goal.id)
        goal.cancelled_at = None                          # store from an older build

        g.generate_from_maintenance("prune_stale", "stale entities")
        assert len(_active(g)) == 1

    def test_an_explicit_request_is_not_silenced_by_its_own_tombstone(self, tmp_path):
        """The cooldown silences drives, not people. Asking for the same thing
        again is how an operator undoes cancelling it."""
        g = GoalGenerator(tmp_path)
        goal = g.add_user_goal("audit the ledger")
        g.cancel_goal(goal.id)

        g.add_user_goal("audit the ledger")
        assert len(_active(g)) == 1

    def test_completed_and_expired_checks_stay_regenerable(self, tmp_path):
        """A check that ran should run again when its condition returns."""
        g = GoalGenerator(tmp_path)
        done = g.generate_from_maintenance("prune_stale", "stale entities")
        g.complete_goal(done.id)
        g.generate_from_maintenance("prune_stale", "stale entities again")
        assert len(_active(g)) == 1

        _active(g)[0].status = "expired"
        g.generate_from_maintenance("prune_stale", "stale entities once more")
        assert len(_active(g)) == 1


class TestDedupKey:
    def test_maintenance_keys_on_check_type(self, tmp_path):
        g = GoalGenerator(tmp_path)
        goal = g.generate_from_maintenance("prune_stale", "whatever")
        assert goal.dedup_key == "maintenance:prune_stale"

    def test_other_drives_key_on_description(self, tmp_path):
        g = GoalGenerator(tmp_path)
        goal = g.generate_from_curiosity("why did X spike")
        assert goal.drive is Drive.CURIOSITY
        assert goal.dedup_key == goal.description
