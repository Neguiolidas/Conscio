# tests/test_dream_distill.py
"""Distill — the dream's procedural consolidation sub-phase (spec v1.1
section 4). Runs last, reads only the ActionLedger, writes only skills;
a passive engine (no attached pipeline) dreams exactly as in v1.0."""
import json
from types import SimpleNamespace

import pytest

from conscio.agency.act import goal_fingerprint
from conscio.agency.ledger import ActionLedger
from conscio.agency.skills import SkillLibrary
from conscio.engine import ConsciousnessEngine
from conscio.content_layer import _RAG_DISABLED

GOAL = "Investigate: anomaly in sandbox notes"


@pytest.fixture
def engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    e.content_layer._session_rag = _RAG_DISABLED
    yield e
    e.close()


def _attach_fakes(engine, tmp_path):
    """Minimal volition stubs: real ledger + real SkillLibrary on the
    engine's shared conscio.db, no LLM involved."""
    db = tmp_path / "conscio.db"
    ledger = ActionLedger(db)
    engine._skills = SkillLibrary(db)
    engine._act_pipeline = SimpleNamespace(ledger=ledger)
    return ledger


def _seed_success(ledger):
    ledger.record(goal_fp=goal_fingerprint(GOAL), goal_text=GOAL,
                  tool="fs_read", args_json=json.dumps({"path": "n.md"}),
                  rationale="inspect", tier="T2", status="executed", ok=True)


class TestPassiveEngine:
    def test_dream_without_pipeline_distills_nothing(self, engine):
        report = engine.dream()
        assert report.skills_distilled == 0
        assert report.to_dict()["skills_distilled"] == 0


class TestDistillPhase:
    def test_dream_distills_ledger_successes_into_skills(self, engine,
                                                         tmp_path):
        ledger = _attach_fakes(engine, tmp_path)
        _seed_success(ledger)
        report = engine.dream()
        assert report.skills_distilled == 1
        assert engine._skills.count() == 1

    def test_second_dream_without_new_actions_distills_zero(self, engine,
                                                            tmp_path):
        ledger = _attach_fakes(engine, tmp_path)
        _seed_success(ledger)
        engine.dream()
        report = engine.dream()
        assert report.skills_distilled == 0          # A7: watermark

    def test_dry_run_counts_without_writing(self, engine, tmp_path):
        ledger = _attach_fakes(engine, tmp_path)
        _seed_success(ledger)
        report = engine.dream(dry_run=True)
        assert report.skills_distilled == 1
        assert engine._skills.count() == 0           # nothing persisted
        report2 = engine.dream()                     # watermark intact
        assert report2.skills_distilled == 1
