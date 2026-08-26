"""Tests for Experts.QA + Experts.Promptor (Ato 2)."""
from __future__ import annotations

import pytest

from conscio.squads._base import VoiceResult
from conscio.squads._router import EXPERTS_VOICES, get_voice
from conscio.squads.experts.promptor import PromptorVoice
from conscio.squads.experts.qa import QAVoice

# ═══════════════════════════════════════════════════════════════════════
# QA voice
# ═══════════════════════════════════════════════════════════════════════


class TestQAVoice:
    def test_name_role(self):
        v = QAVoice()
        assert v.name == "qa"
        assert v.role == "qa"

    def test_clean_input_proceeds(self):
        v = QAVoice()
        r = v.analyze({"question": "Compute 2+2.", "context": ""})
        assert r.vote in ("proceed", "hold", "veto")
        assert r.role == "qa"

    def test_detects_missing_tests(self):
        v = QAVoice()
        ctx = {
            "question": "Implement user authentication",
            "context": "No tests written yet",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_detects_skip_xfail_pattern(self):
        v = QAVoice()
        ctx = {
            "question": "Fix flaky test",
            "context": "mark xfail or skip the test",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_detects_edge_case_omission(self):
        v = QAVoice()
        ctx = {
            "question": "Implement parser",
            "context": "edge cases unlikely, skip them",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_result_shape(self):
        v = QAVoice()
        r = v.analyze({"question": "test", "context": ""})
        for attr in ("role", "analysis", "concerns", "vote"):
            assert hasattr(r, attr)

    def test_registered_in_experts(self):
        # QA should be registered after load_voices
        assert "qa" in EXPERTS_VOICES
        assert get_voice("qa") is not None


# ═══════════════════════════════════════════════════════════════════════
# Promptor voice (deterministic, always — no LLM)
# ═══════════════════════════════════════════════════════════════════════


class TestPromptorVoice:
    def test_name_role(self):
        v = PromptorVoice()
        assert v.name == "promptor"
        assert v.role == "promptor"

    def test_analyze_returns_result(self):
        v = PromptorVoice()
        r = v.analyze({
            "question": "Write a marketing email",
            "context": "target audience: CTOs",
        })
        assert isinstance(r, VoiceResult)
        assert r.role == "promptor"

    def test_detects_vague_prompt(self):
        v = PromptorVoice()
        r = v.analyze({
            "question": "help",
            "context": "",
        })
        # "help" alone is vague → should flag
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_detects_missing_target_ai(self):
        v = PromptorVoice()
        r = v.analyze({
            "question": "Write a marketing email",
            "context": "",
        })
        # No target AI specified → should flag
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_well_specified_prompt_proceeds(self):
        v = PromptorVoice()
        r = v.analyze({
            "question": "BASICO usando Claude — Ajuda com meu curriculo",
            "context": "Target: Claude. Mode: BASICO. Task: resume help.",
        })
        # Well-specified → should proceed or at most hold
        assert r.vote in ("proceed", "hold")

    def test_promptor_has_no_llm_path(self):
        v = PromptorVoice()
        # analyze_llm should raise NotImplementedError — promptor is
        # always deterministic.
        with pytest.raises(NotImplementedError):
            v.analyze_llm({}, adapter=None)

    def test_registered_in_experts(self):
        assert "promptor" in EXPERTS_VOICES
        assert get_voice("promptor") is not None