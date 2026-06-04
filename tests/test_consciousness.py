"""
Tests for ConsciousnessRecognition framework.
"""

import json
import tempfile
from pathlib import Path

import pytest

from conscio.models import ModelRegistry, ContextMode, ModelInfo
from conscio.context_manager import ContextManager, ConsciousnessState, MODE_BUDGETS
from conscio.world_model import WorldModel
from conscio.meta_cognition import MetaCognition
from conscio.goal_generator import GoalGenerator, Drive, GoalPriority
from conscio.auto_evolution import AutoEvolution, EvolutionType, ProposalStatus
from conscio.engine import ConsciousnessEngine


# --- Fixtures ---

@pytest.fixture
def tmp_storage(tmp_path):
    return tmp_path / "consciousness"


@pytest.fixture
def ctx_manager(tmp_storage):
    return ContextManager("glm-5.1", storage_path=tmp_storage)


@pytest.fixture
def world_model(tmp_storage):
    return WorldModel(tmp_storage)


@pytest.fixture
def meta_cognition(tmp_storage):
    return MetaCognition(tmp_storage)


@pytest.fixture
def goal_generator(tmp_storage):
    return GoalGenerator(tmp_storage)


@pytest.fixture
def auto_evolution(tmp_storage):
    return AutoEvolution(tmp_storage)


@pytest.fixture
def engine(tmp_storage):
    return ConsciousnessEngine("glm-5.1", storage_path=tmp_storage)


# --- Model Registry Tests ---

class TestModelRegistry:
    def test_lookup_exact(self):
        info = ModelRegistry.lookup("glm-5.1")
        assert info is not None
        assert info.context_window == 131_000
        assert info.mode == ContextMode.COMPACT

    def test_lookup_alias(self):
        info = ModelRegistry.lookup("glm")
        assert info is not None
        assert info.name == "glm-5.1"

    def test_lookup_unknown(self):
        info = ModelRegistry.lookup("unknown-model-xyz")
        assert info is None

    def test_detect_with_context_window(self):
        info = ModelRegistry.detect("unknown-model", context_window=300_000)
        assert info.context_window == 300_000
        assert info.mode == ContextMode.STANDARD

    def test_detect_mode_thresholds(self):
        assert ModelRegistry.detect_mode(64_000) == ContextMode.MINIMAL
        assert ModelRegistry.detect_mode(128_000) == ContextMode.COMPACT
        assert ModelRegistry.detect_mode(256_000) == ContextMode.STANDARD
        assert ModelRegistry.detect_mode(1_000_000) == ContextMode.STANDARD

    def test_extract_context_from_name(self):
        assert ModelRegistry._extract_context_from_name("model-128k") == 128_000
        assert ModelRegistry._extract_context_from_name("model-1m") == 1_000_000
        assert ModelRegistry._extract_context_from_name("no-context-here") == 128_000  # default

    def test_register_new_model(self):
        info = ModelRegistry.register("test-model", 500_000, ["test"])
        assert info.context_window == 500_000
        assert info.mode == ContextMode.STANDARD
        # Can look it up now
        assert ModelRegistry.lookup("test-model") is not None

    def test_model_available_context(self):
        info = ModelRegistry.lookup("glm-5.1")
        # Should be ~80% of 131k
        assert 100_000 < info.available_context_tokens < 110_000


# --- Context Manager Tests ---

class TestContextManager:
    def test_compact_mode_for_glm(self, ctx_manager):
        assert ctx_manager.mode == ContextMode.COMPACT
        assert ctx_manager.max_injection_tokens == 500

    def test_build_state_trims_to_budget(self, ctx_manager):
        long_text = "word " * 1000  # Way over budget
        state = ctx_manager.build_state(
            state_summary=long_text,
            last_reflection=long_text,
            active_goals=["goal1", "goal2", "goal3", "goal4", "goal5", "goal6"],
        )
        # Goals should be limited to 3 in compact mode
        assert len(state.active_goals) <= 3
        # Total tokens should be within budget (with some margin)
        assert state.total_tokens_approx() < ctx_manager.max_injection_tokens * 1.5

    def test_minimal_mode_no_reflection(self, tmp_storage):
        cm = ContextManager("unknown-model", context_window=64_000, storage_path=tmp_storage)
        state = cm.build_state(
            state_summary="Some summary",
            last_reflection="Some reflection",
            active_goals=["goal1"],
        )
        assert state.context_mode == ContextMode.MINIMAL
        # Reflection and goals should not be in injection in minimal mode
        injection = state.to_injection()
        assert "Last reflection" not in injection

    def test_save_and_load_state(self, ctx_manager):
        state = ctx_manager.build_state(
            state_summary="Test state summary",
            meta_cognition="Confidence: 80%",
        )
        path = ctx_manager.save_state(state)
        assert path.exists()

        loaded = ctx_manager.load_state()
        assert "Test state summary" in loaded.state_summary

    def test_injection_format(self, ctx_manager):
        state = ctx_manager.build_state(
            state_summary="I am running on GLM 5.1",
            meta_cognition="Confidence: high",
        )
        injection = state.to_injection()
        assert "CONSCIOUSNESS STATE" in injection
        assert "compact" in injection
        assert "GLM 5.1" in injection.lower() or "glm-5.1" in injection.lower()


# --- World Model Tests ---

class TestWorldModel:
    def test_add_and_get_entity(self, world_model):
        world_model.add_entity("trading_bot", "system", state="running")
        entity = world_model.get_entity("trading_bot")
        assert entity is not None
        assert entity["type"] == "system"
        assert entity["state"] == "running"

    def test_add_relation(self, world_model):
        world_model.add_entity("user", "person")
        world_model.add_entity("trading_bot", "system")
        world_model.add_relation("user", "owns", "trading_bot")
        relations = world_model.get_relations("user")
        assert len(relations) == 1
        assert relations[0]["relation"] == "owns"

    def test_query(self, world_model):
        world_model.add_entity("BTC", "asset", state="price 67000")
        result = world_model.query("BTC")
        assert "BTC" in result

    def test_predictions(self, world_model):
        world_model.add_prediction("BTC drops below 65k", "Sell signal triggered", 0.6)
        preds = world_model.get_predictions("BTC")
        assert len(preds) == 1
        assert preds[0]["confidence"] == 0.6

    def test_stale_entities(self, world_model):
        world_model.add_entity("stale_entity", "test", state="old")
        # Manually set old timestamp
        import datetime
        entity = world_model._data["entities"]["stale_entity"]
        entity["last_updated"] = (datetime.datetime.now() - datetime.timedelta(hours=48)).isoformat()
        world_model._save()
        stale = world_model.stale_entities(max_age_hours=24)
        assert "stale_entity" in stale


# --- Meta-Cognition Tests ---

class TestMetaCognition:
    def test_record_confidence(self, meta_cognition):
        meta_cognition.record_confidence("coding", 0.9, "success")
        assert meta_cognition.average_confidence("coding") == 0.9

    def test_accuracy(self, meta_cognition):
        meta_cognition.record_confidence("coding", 0.9, "success")
        meta_cognition.record_confidence("coding", 0.7, "failure")
        assert meta_cognition.accuracy("coding") == 0.5

    def test_blind_spot_detection(self, meta_cognition):
        # Low confidence → blind spot
        for _ in range(3):
            meta_cognition.record_confidence("weak_area", 0.3, "failure")
        assert "weak_area" in meta_cognition._data["blind_spots"]

    def test_error_pattern_tracking(self, meta_cognition):
        meta_cognition.record_error("Forgot to check API rate limit")
        meta_cognition.record_error("Forgot to check API rate limit")
        freq = meta_cognition.frequent_errors(min_count=2)
        assert len(freq) == 1
        assert freq[0]["count"] == 2

    def test_summary(self, meta_cognition):
        meta_cognition.record_confidence("general", 0.8, "success")
        summary = meta_cognition.summary()
        assert "Confidence" in summary


# --- Goal Generator Tests ---

class TestGoalGenerator:
    def test_curiosity_goal(self, goal_generator):
        goal = goal_generator.generate_from_curiosity("Unusual order book pattern")
        assert goal is not None
        assert "Investigate" in goal.description
        assert goal.drive == Drive.CURIOSITY

    def test_maintenance_goal(self, goal_generator):
        goal = goal_generator.generate_from_maintenance("health_check", "trading_bot")
        assert goal is not None
        assert "Maintenance" in goal.description

    def test_max_active_goals(self, goal_generator):
        # Add more than max goals
        for i in range(15):
            goal_generator.generate_from_curiosity(f"Anomaly {i}")
        active = goal_generator.active_goals()
        assert len(active) <= GoalGenerator.MAX_ACTIVE_GOALS

    def test_complete_goal(self, goal_generator):
        goal = goal_generator.generate_from_curiosity("Test anomaly")
        assert goal is not None
        result = goal_generator.complete_goal(goal.id)
        assert result is True

    def test_summary(self, goal_generator):
        goal_generator.generate_from_curiosity("Test anomaly")
        summary = goal_generator.summary()
        assert len(summary) > 0


# --- Auto Evolution Tests ---

class TestAutoEvolution:
    def test_propose_skill_patch(self, auto_evolution):
        proposal = auto_evolution.propose_skill_patch(
            skill_name="test-skill",
            issue="Missing error handling",
            suggested_fix="Add try/except block",
            rationale="Observed 3 failures due to unhandled exceptions",
        )
        assert proposal.status == ProposalStatus.PENDING
        assert "test-skill" in proposal.description

    def test_approve_proposal(self, auto_evolution):
        proposal = auto_evolution.propose_skill_patch(
            skill_name="test",
            issue="Bug",
            suggested_fix="Fix",
            rationale="It's broken",
        )
        approved = auto_evolution.approve(proposal.id)
        assert approved is not None
        assert approved.status == ProposalStatus.APPROVED

    def test_reject_proposal(self, auto_evolution):
        proposal = auto_evolution.propose_skill_patch(
            skill_name="test",
            issue="Minor issue",
            suggested_fix="Ignore",
            rationale="Not important",
        )
        rejected = auto_evolution.reject(proposal.id, "Too risky")
        assert rejected is not None
        assert rejected.status == ProposalStatus.REJECTED

    def test_pending_proposals(self, auto_evolution):
        auto_evolution.propose_skill_patch("t1", "i1", "f1", "r1")
        auto_evolution.propose_skill_patch("t2", "i2", "f2", "r2")
        pending = auto_evolution.pending_proposals()
        assert len(pending) >= 2


# --- Engine Integration Tests ---

class TestConsciousnessEngine:
    def test_initialization(self, engine):
        assert engine.mode == ContextMode.COMPACT
        assert engine.model_info.name == "glm-5.1"

    def test_reflect(self, engine):
        result = engine.reflect(
            world_state="All systems nominal",
            confidence=0.8,
        )
        assert "summary" in result
        assert len(result["summary"]) > 0

    def test_state_injection(self, engine):
        engine.reflect(world_state="Test", confidence=0.7)
        injection = engine.get_state_for_injection()
        assert "CONSCIOUSNESS STATE" in injection
        assert "compact" in injection

    def test_status(self, engine):
        status = engine.status()
        assert "model" in status
        assert "mode" in status
        assert status["mode"] == "compact"

    def test_health_check(self, engine):
        health = engine.health_check()
        assert health["healthy"] is True

    def test_perceive(self, engine):
        engine.perceive(
            world_state="Market open",
            entities={
                "BTC": {"type": "asset", "state": "price 67000"},
            }
        )
        entity = engine.world.get_entity("BTC")
        assert entity is not None
        assert entity["state"] == "price 67000"
