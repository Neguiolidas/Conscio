# tests/test_agency_skills_wiring.py
"""F4 engine integration: SkillLibrary created at attach_adapter, few-shot
exemplars reach the actor prompt (A8), act() settles outcomes back into
the served skills (A12), close() releases the library."""
import json
import sqlite3

import pytest

from conscio import ConsciousnessEngine
from conscio.engine import _RAG_DISABLED
from conscio.agency.act import ActReport, ActStatus, goal_fingerprint
from conscio.agency.adapter import MockAdapter
from conscio.agency.skills import SkillLibrary
from conscio.context_manager import ConsciousnessState

GOAL = "write a memory note about the anomaly"
CHECKLIST_PASS = "A1: NO\nA2: NO\nA3: YES"


def _proposal(tool="memory_note", args=None):
    return json.dumps({"tool": tool, "args": args or {"text": "n"},
                       "rationale": "r", "expected_outcome": "e"})


def _state():
    return ConsciousnessState(state_summary="s", active_goals=[GOAL],
                              coherence_note="epistemic")


def _seed_skill(engine):
    """One distilled skill for GOAL via the real ledger + a dream."""
    engine._act_pipeline.ledger.record(
        goal_fp=goal_fingerprint(GOAL), goal_text=GOAL, tool="memory_note",
        args_json=json.dumps({"text": "n"}), rationale="works", tier="T2",
        status="executed", ok=True)
    engine.dream()


class TestWiring:
    def test_attach_creates_skill_library_and_provider(self, tmp_path):
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            pipe = eng.attach_adapter(MockAdapter(script=[]),
                                      sandbox_root=tmp_path / "sb")
            assert isinstance(eng._skills, SkillLibrary)
            assert pipe.few_shot_provider is not None

    def test_engine_state_is_public_view(self, tmp_path):
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            assert eng.state is eng._state


class TestFewShotInActorPrompt:
    def test_seeded_skill_reaches_actor_prompt(self, tmp_path):
        adapter = MockAdapter(script=[_proposal(), CHECKLIST_PASS])
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            eng.content_layer._session_rag = _RAG_DISABLED
            eng.attach_adapter(adapter, sandbox_root=tmp_path / "sb")
            _seed_skill(eng)
            report = eng.act(_state())
            assert report.status is ActStatus.PROPOSED
            actor_prompt = adapter.calls[0]["prompt"]
            assert "Examples of past successful actions:" in actor_prompt
            assert "Past successful plan" in actor_prompt
            assert '"tool": "memory_note"' in actor_prompt   # T2 JSON render

    def test_no_skills_means_no_exemplars(self, tmp_path):
        adapter = MockAdapter(script=[_proposal(), CHECKLIST_PASS])
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            eng.content_layer._session_rag = _RAG_DISABLED
            eng.attach_adapter(adapter, sandbox_root=tmp_path / "sb")
            eng.act(_state())
            prompt = adapter.calls[0]["prompt"]
            assert "Examples of past successful actions:" not in prompt


class TestSettleWiring:
    def test_act_settles_outcome_into_served_skills(self, tmp_path):
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            eng.content_layer._session_rag = _RAG_DISABLED
            eng.attach_adapter(MockAdapter(script=[]),
                               sandbox_root=tmp_path / "sb")
            _seed_skill(eng)
            eng._skills.few_shot(GOAL, tier="T2")     # serve -> slot set
            eng._act_pipeline.act = lambda state: ActReport(
                status=ActStatus.EXECUTED)
            eng.act(_state())
            [skill] = eng._skills.all()
            assert skill["successes"] == 2            # distill + settle


class TestClose:
    def test_close_releases_skill_library(self, tmp_path):
        eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
        eng.attach_adapter(MockAdapter(script=[]),
                           sandbox_root=tmp_path / "sb")
        eng.close()
        with pytest.raises(sqlite3.ProgrammingError):
            eng._skills.count()
