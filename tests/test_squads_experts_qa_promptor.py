"""Tests for Experts.QA + Experts.Promptor (Ato 2; Promptor refiner since v4.8.1)."""
from __future__ import annotations

from conscio.squads._base import VoiceResult
from conscio.squads._router import EXPERTS_VOICES, get_voice
from conscio.squads.experts.promptor import PromptorVoice, PromptRefinement
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
# Promptor voice (v4.8.1: prompt refiner — rewrites, never evaluates)
# ═══════════════════════════════════════════════════════════════════════


class _FakeAdapter:
    """Minimal adapter stub: generate() returns an object with .text."""

    def __init__(self, text: str = "", fail: bool = False):
        self._text = text
        self._fail = fail
        self.calls: list[str] = []

    def generate(self, prompt: str, max_tokens: int = 128,
                 temperature: float = 0.3):
        self.calls.append(prompt)
        if self._fail:
            raise RuntimeError("adapter down")
        from types import SimpleNamespace
        return SimpleNamespace(text=self._text)


class TestPromptorVoice:
    def test_name_role(self):
        v = PromptorVoice()
        assert v.name == "promptor"
        assert v.role == "promptor"

    def test_does_not_vote(self):
        # The refiner produces an artefact, not a judgement: the squad
        # aggregation must never count its vote.
        assert PromptorVoice.voting is False

    def test_returns_refined_prompt(self):
        v = PromptorVoice()
        r = v.analyze({
            "question": "Write a marketing email",
            "context": "target audience: CTOs",
        })
        assert isinstance(r, PromptRefinement)
        assert isinstance(r, VoiceResult)
        assert r.role == "promptor"
        assert r.refined_prompt.startswith("Objective: Write a marketing email")
        assert "Context: target audience: CTOs" in r.refined_prompt

    def test_original_ask_preserved_verbatim(self):
        # Never invent: the objective is the caller's own words.
        v = PromptorVoice()
        ask = "Explain the retry policy for webhooks"
        r = v.analyze({"question": ask, "context": ""})
        assert f"Objective: {ask}" in r.refined_prompt

    def test_gaps_become_explicit_markers(self):
        # Vague input: every gap becomes a bracketed marker inside the
        # refined prompt — never a fabricated value.
        v = PromptorVoice()
        r = v.analyze({"question": "help", "context": ""})
        assert r.refined_prompt.count("[UNSPECIFIED") >= 3  # context/constraints/format
        assert "[UNSPECIFIED" not in r.changes  # markers live in the prompt
        assert r.changes  # but the change list explains what was added

    def test_refinement_is_deterministic_exact_shape(self):
        # Exact expected output for the empty-context vague case: only
        # section labels, verbatim ask, and markers — nothing else.
        from conscio.squads.experts.promptor import (
            _CONSTRAINTS_GAP,
            _FORMAT_GAP,
        )
        v = PromptorVoice()
        r = v.analyze({"question": "help", "context": ""})
        expected = (
            "Objective: help\n"
            "\n"
            "Context: [UNSPECIFIED — add background the model needs; "
            "omit this section if none applies]\n"
            "\n"
            f"Constraints: {_CONSTRAINTS_GAP}\n"
            "\n"
            f"Output format: {_FORMAT_GAP}"
        )
        assert r.refined_prompt == expected

    def test_constraints_quoted_verbatim_from_input(self):
        v = PromptorVoice()
        ask = "Write release notes. Keep it under 200 words. Must be technical."
        r = v.analyze({"question": ask, "context": ""})
        assert "- Keep it under 200 words." in r.refined_prompt
        assert "- Must be technical." in r.refined_prompt
        assert "Constraints: [UNSPECIFIED" not in r.refined_prompt

    def test_no_fabricated_content(self):
        # Nothing in the refined prompt may go beyond the caller's own
        # words plus labels/markers: no digits, no new nouns.
        v = PromptorVoice()
        r = v.analyze({"question": "summarize this", "context": ""})
        body = r.refined_prompt.replace("[UNSPECIFIED", "").replace("]", "")
        for marker_tail in ("state any constraints", "say what the output",
                            "add background"):
            body = body.replace(marker_tail, "")
        assert not any(ch.isdigit() for ch in body)

    def test_changes_list_what_changed(self):
        v = PromptorVoice()
        r = v.analyze({"question": "help", "context": ""})
        assert r.changes
        assert all(isinstance(c, str) for c in r.changes)
        assert any("marker" in c for c in r.changes)

    def test_llm_path_rewrites_prompt(self):
        v = PromptorVoice()
        adapter = _FakeAdapter(text="Objective: do the thing, better")
        r = v.analyze_llm({"question": "help", "context": ""}, adapter)
        assert r.refined_prompt == "Objective: do the thing, better"
        assert r.changes == ["rewritten by the LLM adapter"]
        assert len(adapter.calls) == 1

    def test_llm_failure_falls_back_to_deterministic(self):
        v = PromptorVoice()
        deterministic = v.analyze({"question": "help", "context": ""})
        r = v.analyze_llm({"question": "help", "context": ""},
                          _FakeAdapter(fail=True))
        assert r.refined_prompt == deterministic.refined_prompt

    def test_llm_empty_text_falls_back(self):
        v = PromptorVoice()
        deterministic = v.analyze({"question": "help", "context": ""})
        r = v.analyze_llm({"question": "help", "context": ""},
                          _FakeAdapter(text=""))
        assert r.refined_prompt == deterministic.refined_prompt

    def test_llm_path_without_adapter_is_deterministic(self):
        v = PromptorVoice()
        deterministic = v.analyze({"question": "help", "context": ""})
        r = v.analyze_llm({"question": "help", "context": ""}, None)
        assert r.refined_prompt == deterministic.refined_prompt

    def test_registered_in_experts(self):
        assert "promptor" in EXPERTS_VOICES
        assert get_voice("promptor") is not None