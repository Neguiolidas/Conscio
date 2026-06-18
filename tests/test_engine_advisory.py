# tests/test_engine_advisory.py
"""v1.6 (#5/#9): engine.advisory() — the host's pull surface.

The daemon perceives -> reflects -> acts, but the host had no cheap, documented
way to read what Conscio concluded, so awake output died unread. advisory() is a
read-only, no-LLM, no-mutation structured snapshot the host pulls each turn:
cognitive state + goals tagged by provenance (executable vs diagnostic, #7) +
operational status (lockdown / failure-brake, #8).
"""
import pytest

from conscio.content_layer import _RAG_DISABLED
from conscio.engine import ConsciousnessEngine
from conscio.goal_generator import GoalOrigin


@pytest.fixture
def engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    e.content_layer._session_rag = _RAG_DISABLED   # hermetic: no Ollama probe
    yield e
    e.close()


class TestAdvisoryShape:
    def test_core_keys_present(self, engine):
        adv = engine.advisory()
        for key in ("awake", "reflection", "goals", "coherence",
                    "status", "recommendations"):
            assert key in adv, key
        for key in ("action_lockdown", "dream_recommended", "brake"):
            assert key in adv["status"], key

    def test_goals_is_a_list_of_tagged_dicts(self, engine):
        engine.goals.add_user_goal("ship the release")
        adv = engine.advisory()
        assert adv["goals"]
        g = adv["goals"][0]
        assert set(g) == {"description", "origin", "executable"}


class TestAdvisoryAwake:
    def test_tracks_awake_state(self, engine):
        engine.sleep()
        assert engine.advisory()["awake"] is False
        engine.wake()
        assert engine.advisory()["awake"] is True


class TestAdvisoryProvenance:
    def test_tags_executable_and_diagnostic_goals(self, engine):
        engine.goals.add_user_goal("real user task")            # executable
        engine.goals.generate_from_curiosity("a thought",
                                             source="self_prompt")  # diagnostic
        by_desc = {g["description"]: g for g in engine.advisory()["goals"]}
        user = next(g for d, g in by_desc.items() if "real user task" in d)
        diag = next(g for d, g in by_desc.items() if "a thought" in d)
        assert user["executable"] is True
        assert user["origin"] == GoalOrigin.USER.value
        assert diag["executable"] is False
        assert diag["origin"] == GoalOrigin.SELF_PROMPT.value

    def test_recommends_review_of_diagnostic_goals(self, engine):
        engine.goals.generate_from_curiosity("introspect", source="self_prompt")
        recs = " ".join(engine.advisory()["recommendations"]).lower()
        assert "diagnostic" in recs


class TestAdvisoryStatus:
    def test_reports_action_lockdown(self, engine):
        engine.state.action_lockdown = True
        assert engine.advisory()["status"]["action_lockdown"] is True

    def test_surfaces_failure_brake(self, engine):
        # The aggregate brake (#8) emits a system event when it trips; the host
        # reads it from the advisory rather than tailing the event log.
        engine.event_bus.emit(
            type="system", category="system",
            data={"message": "failure-rate brake: autonomous loop stopped",
                  "failures": 4, "cycles": 4, "failure_rate": 1.0},
            priority=8)
        assert "failure-rate brake" in (engine.advisory()["status"]["brake"] or "")

    def test_no_brake_when_none_emitted(self, engine):
        assert engine.advisory()["status"]["brake"] is None


class TestAdvisoryIsCheap:
    def test_read_only_no_adapter_required(self, engine):
        # No adapter is attached -> if advisory() called inference it would fail.
        # It must return cleanly and not mutate the active-goal count.
        engine.goals.add_user_goal("task")
        before = len(engine.goals.active_goals())
        engine.advisory()
        engine.advisory()
        assert len(engine.goals.active_goals()) == before
