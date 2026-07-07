# tests/test_agency_actor.py
"""Tests for the stateless actor prompt assembly (spec section 5.5)."""
from conscio.agency.actor import ACTOR_PERSONA, build_actor_prompt
from conscio.context_manager import ConsciousnessState


def _state(**kw):
    return ConsciousnessState(state_summary="I run the homelab",
                              coherence_note="epistemic", **kw)


class TestBuildActorPrompt:
    def test_contains_all_semantic_sections(self):
        prompt = build_actor_prompt(
            state=_state(), goal_text="organize notes",
            catalog_text="- fs_read(path:str) [low] — read file",
            recall_snippets=["yesterday: notes were messy"],
            few_shot=[])
        assert ACTOR_PERSONA.splitlines()[0] in prompt
        assert "organize notes" in prompt
        assert "epistemic" in prompt              # dominant dissonance
        assert "fs_read" in prompt                # tool catalog
        assert "notes were messy" in prompt       # recall RAG

    def test_stateless_no_history_between_builds(self):
        first = build_actor_prompt(state=_state(), goal_text="UNIQUE_A",
                                   catalog_text="", recall_snippets=[],
                                   few_shot=[])
        second = build_actor_prompt(state=_state(), goal_text="UNIQUE_B",
                                    catalog_text="", recall_snippets=[],
                                    few_shot=[])
        assert "UNIQUE_A" not in second
        assert "UNIQUE_B" not in first

    def test_few_shot_hook_renders_examples(self):
        prompt = build_actor_prompt(
            state=_state(), goal_text="g", catalog_text="",
            recall_snippets=[], few_shot=["EXAMPLE: previous good plan"])
        assert "previous good plan" in prompt

    def test_empty_recall_section_is_omitted(self):
        prompt = build_actor_prompt(state=_state(), goal_text="g",
                                    catalog_text="", recall_snippets=[],
                                    few_shot=[])
        assert "Relevant memories" not in prompt

    def test_intercept_enabled_adds_section(self):
        prompt = build_actor_prompt(
            state=_state(), goal_text="g", catalog_text="",
            recall_snippets=[], few_shot=[],
            intercept_enabled=True)
        assert "[INTERCEPT:" in prompt
        assert "Deterministic Computation" in prompt

    def test_intercept_disabled_omits_section(self):
        prompt = build_actor_prompt(
            state=_state(), goal_text="g", catalog_text="",
            recall_snippets=[], few_shot=[],
            intercept_enabled=False)
        assert "[INTERCEPT:" not in prompt
        assert "Deterministic Computation" not in prompt
