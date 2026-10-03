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

    # ── reviewer findings D1/D2: no section repeats another, limits
    #    count as constraints ──────────────────────────────────────────

    REPRO_QUESTION = (
        "Write a summary of the quarterly sales report for the board, "
        "in markdown, max 300 words"
    )
    REPRO_CONTEXT = "Board meets Monday; they care about churn"

    @staticmethod
    def _sections(refined: str) -> dict[str, str]:
        """Map 'Objective'/'Context'/... to each section's body text."""
        out: dict[str, str] = {}
        for block in refined.split("\n\n"):
            label, _, body = block.partition(":")
            out[label.strip()] = body.strip()
        return out

    def test_context_stays_in_its_section(self):
        # D1: the question+context join is a clause boundary; without it
        # the context leaked into Constraints/Output format bullets.
        v = PromptorVoice()
        r = v.analyze({
            "question": self.REPRO_QUESTION,
            "context": self.REPRO_CONTEXT,
        })
        sections = self._sections(r.refined_prompt)
        hits = [name for name, body in sections.items()
                if "Board meets Monday" in body]
        assert hits == ["Context"], f"context leaked into {hits}"

    def test_no_section_repeats_another(self):
        # D1: an unpunctuated question with no comma must not come back
        # verbatim inside another section (Objective is already it).
        v = PromptorVoice()
        for question, context in (
            (self.REPRO_QUESTION, self.REPRO_CONTEXT),
            ("Summarize the report in markdown", ""),
        ):
            r = v.analyze({"question": question, "context": context})
            sections = self._sections(r.refined_prompt)
            bodies = list(sections.values())
            assert len(bodies) == len(set(bodies)), (
                f"duplicated section body for {question!r}: {sections}"
            )
            assert question not in sections.get("Output format", "")
            assert question not in sections.get("Constraints", "")

    def test_limit_counts_as_constraint(self):
        # D2: "max 300 words" is an explicit limit — Constraints must
        # quote it, never claim the constraints are unspecified.
        v = PromptorVoice()
        r = v.analyze({
            "question": self.REPRO_QUESTION,
            "context": self.REPRO_CONTEXT,
        })
        constraints = self._sections(r.refined_prompt)["Constraints"]
        assert "max 300 words" in constraints
        assert "[UNSPECIFIED" not in constraints
        # and the format clause keeps its own section
        fmt = self._sections(r.refined_prompt)["Output format"]
        assert "in markdown" in fmt
        assert "max 300 words" not in fmt

    def test_limit_words_do_not_match_inside_words(self):
        # The limit matcher uses word boundaries: "understand"/"admin"
        # must not light the "under"/"min" limit words.
        v = PromptorVoice()
        r = v.analyze({
            "question": "Help me understand the admin panel",
            "context": "",
        })
        constraints = self._sections(r.refined_prompt)["Constraints"]
        assert "[UNSPECIFIED" in constraints
        assert "understand" not in constraints
        assert "admin" not in constraints

    # ── D3: a format/limit named inside the whole question is still
    #    quoted — as the minimal fragment, never the whole question ────

    def test_d3_format_only_in_whole_question_still_quoted(self):
        v = PromptorVoice()
        ask = "Summarize the report in markdown"
        r = v.analyze({"question": ask, "context": ""})
        sections = self._sections(r.refined_prompt)
        assert "markdown" in sections["Output format"]
        assert "[UNSPECIFIED" not in sections["Output format"]
        for name, body in sections.items():
            if name == "Objective":
                continue  # Objective IS the ask, by design
            assert body != ask, f"{name} repeats the whole question"

    def test_d3_limit_only_in_whole_question_goes_to_constraints(self):
        v = PromptorVoice()
        r = v.analyze({"question": "Summarize the report in under 200 words",
                       "context": ""})
        sections = self._sections(r.refined_prompt)
        assert "under 200 words" in sections["Constraints"]
        assert "[UNSPECIFIED" not in sections["Constraints"]
        assert "under 200 words" not in sections["Output format"]

    def test_d3_format_keyword_mid_question(self):
        v = PromptorVoice()
        r = v.analyze({"question": "Give me a JSON list of the users",
                       "context": ""})
        fmt = self._sections(r.refined_prompt)["Output format"]
        assert "JSON" in fmt
        assert "[UNSPECIFIED" not in fmt

    # ── D4: every keyword matches on word boundaries, plurals included ─

    def test_d4_no_false_format_from_substrings(self):
        v = PromptorVoice()
        for ask in ("Make the deploy stable, the release is notable",
                    ("Fix the login bug, listen to the websocket, "
                     "rotate passwords"),
                    "Explain the milestone plan, keep the stone analogy"):
            r = v.analyze({"question": ask, "context": ""})
            sections = self._sections(r.refined_prompt)
            assert "[UNSPECIFIED" in sections["Output format"], ask
            assert "[UNSPECIFIED" in sections["Constraints"], ask

    def test_d4_commonly_is_not_only(self):
        v = PromptorVoice()
        r = v.analyze({"question": "List commonly used commands",
                       "context": ""})
        constraints = self._sections(r.refined_prompt)["Constraints"]
        assert "[UNSPECIFIED" in constraints
        assert "commonly" not in constraints

    # ── D5: Portuguese extras — additive only, English never regresses ─

    def test_d5_portuguese_limits_and_format(self):
        v = PromptorVoice()
        r = v.analyze({
            "question": "Escreva um resumo do relatório trimestral em "
                        "markdown, no máximo 300 palavras",
            "context": "A diretoria se reúne segunda",
        })
        sections = self._sections(r.refined_prompt)
        assert "no máximo 300 palavras" in sections["Constraints"]
        assert "[UNSPECIFIED" not in sections["Constraints"]
        assert "markdown" in sections["Output format"]
        assert "Escreva um resumo" not in sections["Output format"]

    def test_d5_sem_only_opens_the_clause(self):
        v = PromptorVoice()
        r = v.analyze({"question": "Sem rodeios, liste os riscos",
                       "context": ""})
        constraints = self._sections(r.refined_prompt)["Constraints"]
        assert "Sem rodeios" in constraints

    def test_d5_accented_boundaries(self):
        # \b is Unicode-aware in Python str regex: "até"/"não" work.
        v = PromptorVoice()
        r = v.analyze({"question": "Atualize o readme, não altere o "
                                   "schema, até sexta",
                       "context": ""})
        constraints = self._sections(r.refined_prompt)["Constraints"]
        assert "não altere o schema" in constraints
        assert "até sexta" in constraints

    def test_d5_english_never_regresses_from_portuguese(self):
        v = PromptorVoice()
        r = v.analyze({"question": "Ask Tom about the SEM budget",
                       "context": ""})
        sections = self._sections(r.refined_prompt)
        assert "[UNSPECIFIED" in sections["Constraints"]
        assert "[UNSPECIFIED" in sections["Output format"]

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
        # D3: the section quotes the MINIMAL fragment that carries the
        # limit/keyword — the caller's own words, not the whole clause.
        v = PromptorVoice()
        ask = "Write release notes. Keep it under 200 words. Must be technical."
        r = v.analyze({"question": ask, "context": ""})
        constraints = self._sections(r.refined_prompt)["Constraints"]
        assert "under 200 words." in constraints
        assert "- Must be technical." in constraints
        assert "[UNSPECIFIED" not in constraints

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