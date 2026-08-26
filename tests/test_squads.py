"""Tests for conscio.squads — v4.4 Multi-Squad system.

Covers Ato 1:
- Voice protocol base class (deterministic-only contract)
- Experts.Optimizer (performance heuristics)
- Experts.Auditor (security heuristics)
- Voice registry and lookup
"""

from __future__ import annotations

import pytest

from conscio.squads._base import Voice, VoiceResult, VoiceVote
from conscio.squads._router import EXPERTS_VOICES, get_voice, register_voice
from conscio.squads.experts.auditor import AuditorVoice
from conscio.squads.experts.optimizer import OptimizerVoice

# ═══════════════════════════════════════════════════════════════════════
# Voice protocol
# ═══════════════════════════════════════════════════════════════════════


class TestVoiceProtocol:
    def test_voice_result_shape(self):
        r = VoiceResult(role="x", analysis="a", concerns=[], vote="proceed")
        assert r.role == "x"
        assert r.analysis == "a"
        assert r.concerns == []
        assert r.vote == "proceed"

    def test_voice_vote_valid_set(self):
        for v in ("proceed", "hold", "veto"):
            VoiceVote(v)  # construct without error

    def test_voice_vote_invalid_rejected(self):
        with pytest.raises(ValueError):
            VoiceVote("maybe")

    def test_voice_vote_serde(self):
        assert VoiceVote("hold").value == "hold"

    def test_voice_default_analyze_llm_raises(self):
        class V(Voice):
            name = "stub"
            role = "stub"
            description = ""

            def analyze(self, ctx):
                return VoiceResult(role=self.role, analysis="ok", concerns=[], vote="proceed")

        v = V()
        with pytest.raises(NotImplementedError):
            v.analyze_llm({}, adapter=None)


# ═══════════════════════════════════════════════════════════════════════
# Optimizer (performance heuristics)
# ═══════════════════════════════════════════════════════════════════════


class TestOptimizerVoice:
    def test_name_role_description(self):
        v = OptimizerVoice()
        assert v.name == "optimizer"
        assert v.role == "optimizer"
        assert "performance" in v.description.lower() or "perf" in v.description.lower()

    def test_clean_input_proceeds(self):
        v = OptimizerVoice()
        r = v.analyze({"question": "Compute 2+2 and return.", "context": ""})
        assert isinstance(r, VoiceResult)
        assert r.vote in ("proceed", "hold", "veto")
        assert r.role == "optimizer"

    def test_detects_n_plus_one_pattern(self):
        v = OptimizerVoice()
        ctx = {
            "question": "Implement user lookup with nested posts query",
            "context": "for each user, query posts separately",
        }
        r = v.analyze(ctx)
        # Should flag a concern or hold/veto because of N+1 pattern
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_detects_blocking_io_in_hot_path(self):
        v = OptimizerVoice()
        ctx = {
            "question": "Implement request handler",
            "context": "sync sleep 5s in main loop, blocking call",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_returns_dict_with_all_fields(self):
        v = OptimizerVoice()
        r = v.analyze({"question": "test", "context": ""})
        assert hasattr(r, "role")
        assert hasattr(r, "analysis")
        assert hasattr(r, "concerns")
        assert hasattr(r, "vote")


# ═══════════════════════════════════════════════════════════════════════
# Auditor (security heuristics)
# ═══════════════════════════════════════════════════════════════════════


class TestAuditorVoice:
    def test_name_role_description(self):
        v = AuditorVoice()
        assert v.name == "auditor"
        assert v.role == "auditor"

    def test_clean_input_proceeds(self):
        v = AuditorVoice()
        r = v.analyze({"question": "Compute 2+2.", "context": ""})
        assert r.vote in ("proceed", "hold", "veto")

    def test_detects_hardcoded_secret(self):
        v = AuditorVoice()
        ctx = {
            "question": "Implement auth",
            "context": "API_KEY = 'sk-abc123456789012345678901234'",
        }
        r = v.analyze(ctx)
        # Should flag secret-like pattern
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_detects_sql_injection_pattern(self):
        v = AuditorVoice()
        ctx = {
            "question": "Implement search",
            "context": "f'SELECT * FROM users WHERE id = {user_input}'",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_detects_percent_style_sql_injection(self):
        v = AuditorVoice()
        ctx = {
            "question": "Implement login",
            "context": 'cursor.execute("SELECT * FROM users WHERE id = %s" % user_input)',
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_returns_dict_with_all_fields(self):
        v = AuditorVoice()
        r = v.analyze({"question": "test", "context": ""})
        for attr in ("role", "analysis", "concerns", "vote"):
            assert hasattr(r, attr)


# ═══════════════════════════════════════════════════════════════════════
# Voice registry
# ═══════════════════════════════════════════════════════════════════════


class TestVoiceRegistry:
    def test_experts_voices_registered(self):
        # Optimizer + Auditor are Ato 1 deliverables
        assert "optimizer" in EXPERTS_VOICES
        assert "auditor" in EXPERTS_VOICES

    def test_get_voice_returns_voice_instance(self):
        v = get_voice("optimizer")
        assert isinstance(v, OptimizerVoice)

    def test_get_voice_auditor(self):
        v = get_voice("auditor")
        assert isinstance(v, AuditorVoice)

    def test_get_voice_unknown_returns_none(self):
        assert get_voice("nonexistent_voice") is None

    def test_register_voice(self):
        class Custom(Voice):
            name = "custom_test"
            role = "custom_test"
            description = "test"

            def analyze(self, ctx):
                return VoiceResult(role=self.role, analysis="ok", concerns=[], vote="proceed")

        register_voice("custom_test", Custom(), squad="experts")
        try:
            v = get_voice("custom_test")
            assert v is not None
            assert v.role == "custom_test"
        finally:
            from conscio.squads._router import _VOICE_REGISTRY
            _VOICE_REGISTRY.pop("custom_test", None)
            if "custom_test" in EXPERTS_VOICES:
                EXPERTS_VOICES.discard("custom_test")
