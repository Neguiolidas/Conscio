"""Tests for MetabolicContext — Noosphere tier model (pure, advisory)."""
import pytest

from conscio.metabolic import MetabolicContext, MetabolicState


@pytest.mark.parametrize("pct_tokens,window,expected", [
    (0, 1000, MetabolicState.VITAL),       # 0%
    (399, 1000, MetabolicState.VITAL),     # 39.9%
    (400, 1000, MetabolicState.ACTIVE),    # 40.0% boundary
    (499, 1000, MetabolicState.ACTIVE),    # 49.9%
    (500, 1000, MetabolicState.FATIGUE),   # 50.0% boundary
    (699, 1000, MetabolicState.FATIGUE),   # 69.9%
    (700, 1000, MetabolicState.CRITICAL),  # 70.0% boundary
    (1000, 1000, MetabolicState.CRITICAL), # 100%
    (5000, 1000, MetabolicState.CRITICAL), # over budget
])
def test_assess_tier_boundaries(pct_tokens, window, expected):
    assert MetabolicContext.assess(pct_tokens, window) is expected


def test_usage_pct_clamped():
    assert MetabolicContext.usage_pct(0, 1000) == 0.0
    assert MetabolicContext.usage_pct(2000, 1000) == 100.0  # capped
    assert MetabolicContext.usage_pct(250, 1000) == 25.0


def test_usage_pct_zero_window_safe():
    assert MetabolicContext.usage_pct(100, 0) == 0.0  # no div-by-zero
    assert MetabolicContext.assess(100, 0) is MetabolicState.VITAL


def test_should_mitosis_at_fatigue_and_above():
    assert MetabolicContext.should_mitosis(MetabolicState.VITAL) is False
    assert MetabolicContext.should_mitosis(MetabolicState.ACTIVE) is False
    assert MetabolicContext.should_mitosis(MetabolicState.FATIGUE) is True
    assert MetabolicContext.should_mitosis(MetabolicState.CRITICAL) is True


def test_should_dream_only_critical():
    assert MetabolicContext.should_dream(MetabolicState.FATIGUE) is False
    assert MetabolicContext.should_dream(MetabolicState.CRITICAL) is True


def test_tier_action_is_nonempty_advisory_text():
    for st in MetabolicState:
        assert isinstance(MetabolicContext.tier_action(st), str)
        assert MetabolicContext.tier_action(st)


def test_context_manager_metabolic_state(tmp_path):
    # No config/host-state isolation needed: detect() is offline-by-default, so a
    # known model resolves to the registry (131k) regardless of the dev's machine.
    from conscio.context_manager import ContextManager
    cm = ContextManager("glm-5.1", storage_path=tmp_path)  # 131k window
    # 80k of 131k ≈ 61% → FATIGUE
    assert cm.metabolic_state(80_000) is MetabolicState.FATIGUE
    # 10k of 131k ≈ 7.6% → VITAL
    assert cm.metabolic_state(10_000) is MetabolicState.VITAL


def test_consciousness_state_renders_metabolic_line():
    from conscio.context_manager import ConsciousnessState
    from conscio.models import ContextMode
    state = ConsciousnessState(
        state_summary="alive", context_mode=ContextMode.STANDARD, metabolic="FATIGUE 61%"
    )
    out = state.to_injection()
    assert "FATIGUE 61%" in out
