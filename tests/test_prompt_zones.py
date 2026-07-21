"""TDD for PromptZones + build_zoned_prompt (v3.1 Ato 1)."""
import pytest
from conscio.prompt_zones import PromptZones, build_zoned_prompt
from conscio.context_manager import ConsciousnessState


def test_prompt_zones_creation():
    pz = PromptZones(stable="persona + tools", volatile="state + goal")
    assert pz.stable == "persona + tools"
    assert pz.volatile == "state + goal"


def test_prompt_zones_full_prompt():
    pz = PromptZones(stable="hello", volatile="world")
    assert pz.full_prompt == "hello\nworld"


def test_prompt_zones_stable_hash():
    pz1 = PromptZones(stable="abc", volatile="x")
    pz2 = PromptZones(stable="abc", volatile="y")
    assert pz1.stable_hash == pz2.stable_hash


def test_prompt_zones_hash_differs_on_stable_change():
    pz1 = PromptZones(stable="abc", volatile="x")
    pz2 = PromptZones(stable="def", volatile="x")
    assert pz1.stable_hash != pz2.stable_hash


def test_build_zoned_prompt_returns_prompt_zones():
    state = ConsciousnessState()
    pz = build_zoned_prompt(
        state=state,
        goal_text="test goal",
        catalog_text="tool1, tool2",
        recall_snippets=["memory1"],
    )
    assert isinstance(pz, PromptZones)


def test_stable_contains_persona_and_tools():
    pz = build_zoned_prompt(
        state=ConsciousnessState(),
        goal_text="g",
        catalog_text="my_tool",
    )
    assert "volition" in pz.stable.lower()
    assert "my_tool" in pz.stable


def test_volatile_contains_state_and_goal():
    pz = build_zoned_prompt(
        state=ConsciousnessState(),
        goal_text="reduce dissonance",
        catalog_text="",
    )
    assert "reduce dissonance" in pz.volatile


def test_volatile_contains_recall_snippets():
    pz = build_zoned_prompt(
        state=ConsciousnessState(),
        goal_text="g",
        catalog_text="",
        recall_snippets=["important memory"],
    )
    assert "important memory" in pz.volatile


def test_volatile_contains_intercept_when_enabled():
    pz = build_zoned_prompt(
        state=ConsciousnessState(),
        goal_text="g",
        catalog_text="",
        intercept_enabled=True,
    )
    assert "INTERCEPT" in pz.volatile


def test_stable_excludes_goal_and_state():
    pz = build_zoned_prompt(
        state=ConsciousnessState(),
        goal_text="secret_goal",
        catalog_text="tool_a",
    )
    assert "secret_goal" not in pz.stable


def test_frozen_dataclass():
    pz = PromptZones(stable="a", volatile="b")
    with pytest.raises(Exception):
        pz.stable = "c"


def test_skill_summary_in_stable_zone():
    """v3.1: skill_summary appears in stable (cacheable) zone."""
    pz = build_zoned_prompt(
        state=ConsciousnessState(),
        goal_text="g",
        catalog_text="tool1",
        skill_summary="- code-review: review code quality\n",
    )
    assert "code-review" in pz.stable
    assert "Available skills" in pz.stable
    assert "code-review" not in pz.volatile


def test_no_skill_summary_when_none():
    """v3.1: no skill_summary section when parameter is None."""
    pz = build_zoned_prompt(
        state=ConsciousnessState(),
        goal_text="g",
        catalog_text="tool1",
        skill_summary=None,
    )
    assert "Available skills" not in pz.stable
