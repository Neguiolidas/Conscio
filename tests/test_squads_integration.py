"""Integration tests for squad wrappers + mode 'high' (Ato 5)."""
from __future__ import annotations

import pytest

from conscio import ConsciousnessEngine
from conscio.mcp.modes import BALANCED_TOOLS, HIGH_TOOLS, MODES
from conscio.squads._router import EXPERTS_VOICES, OPOSITORS_VOICES

# ═══════════════════════════════════════════════════════════════════════
# Engine.squad_experts() / squad_opositors()
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def engine(tmp_path):
    with ConsciousnessEngine(model_name="test", storage_path=str(tmp_path)) as e:
        yield e


class TestEngineSquadExperts:
    def test_squad_experts_returns_dict(self, engine):
        r = engine.squad_experts(question="Optimize query")
        assert isinstance(r, dict)
        assert r["squad"] == "experts"

    def test_squad_experts_has_voices(self, engine):
        r = engine.squad_experts(question="Test")
        assert "voices" in r
        assert len(r["voices"]) > 0

    def test_squad_experts_voice_selection(self, engine):
        r = engine.squad_experts(
            question="Test", voices=["optimizer", "auditor"]
        )
        voice_roles = [v["role"] for v in r["voices"]]
        assert "optimizer" in voice_roles
        assert "auditor" in voice_roles
        assert "qa" not in voice_roles  # not requested

    def test_squad_experts_invalid_voice_ignored(self, engine):
        r = engine.squad_experts(
            question="Test", voices=["optimizer", "nonexistent"]
        )
        voice_roles = [v["role"] for v in r["voices"]]
        assert "optimizer" in voice_roles
        assert "nonexistent" not in voice_roles

    def test_squad_experts_requires_question(self, engine):
        with pytest.raises(ValueError, match="question"):
            engine.squad_experts()

    def test_squad_experts_recommendation(self, engine):
        r = engine.squad_experts(question="Clean decision")
        assert r["recommendation"] in ("proceed", "hold", "veto")

    def test_squad_experts_event_emitted(self, engine):
        engine.squad_experts(question="Test")
        events = engine.event_bus.query(type="squad:experts:convened", limit=5)
        assert len(events) >= 1


class TestEngineSquadOpositors:
    def test_squad_opositors_returns_dict(self, engine):
        r = engine.squad_opositors(question="Challenge premise")
        assert isinstance(r, dict)
        assert r["squad"] == "opositors"

    def test_squad_opositors_has_voices(self, engine):
        r = engine.squad_opositors(question="Test")
        assert "voices" in r
        assert len(r["voices"]) > 0

    def test_squad_opositors_voice_selection(self, engine):
        r = engine.squad_opositors(
            question="Test", voices=["caustic"]
        )
        voice_roles = [v["role"] for v in r["voices"]]
        assert "caustic" in voice_roles
        assert len(voice_roles) == 1

    def test_squad_opositors_requires_question(self, engine):
        with pytest.raises(ValueError, match="question"):
            engine.squad_opositors()

    def test_squad_opositors_recommendation(self, engine):
        r = engine.squad_opositors(question="Build product")
        assert r["recommendation"] in ("proceed", "hold", "veto")

    def test_squad_opositors_event_emitted(self, engine):
        engine.squad_opositors(question="Test")
        events = engine.event_bus.query(type="squad:opositors:convened", limit=5)
        assert len(events) >= 1


# ═══════════════════════════════════════════════════════════════════════
# Mode 'high' + HIGH_TOOLS
# ═══════════════════════════════════════════════════════════════════════


class TestHighMode:
    def test_high_in_modes(self):
        assert "high" in MODES

    def test_high_tools_includes_balanced(self):
        # HIGH must be a superset of BALANCED (nested)
        assert BALANCED_TOOLS.issubset(HIGH_TOOLS)

    def test_high_includes_squad_tools(self):
        assert "conscio_squad_experts" in HIGH_TOOLS
        assert "conscio_squad_opositors" in HIGH_TOOLS

    def test_squad_tools_not_in_balanced(self):
        assert "conscio_squad_experts" not in BALANCED_TOOLS
        assert "conscio_squad_opositors" not in BALANCED_TOOLS

    def test_all_experts_registered(self):
        assert len(EXPERTS_VOICES) == 4
        for name in ("optimizer", "auditor", "qa", "promptor"):
            assert name in EXPERTS_VOICES

    def test_all_opositors_registered(self):
        assert len(OPOSITORS_VOICES) == 4
        for name in ("caustic", "devils_advocate", "skeptic_engineer", "douche_reviewer"):
            assert name in OPOSITORS_VOICES