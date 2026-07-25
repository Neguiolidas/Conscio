"""Tests for council auto-reflect fallback (v3.4.1)."""
from conscio.gates import council


def test_council_auto_reflect_when_no_coherence(tmp_path):
    """Council triggers reflect() automatically when last_coherence is None."""
    from conscio import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name="test", storage_path=str(tmp_path))
    # last_coherence should be None on fresh engine
    assert eng.last_coherence is None
    result = council(eng, question="Should we proceed?")
    # After auto-reflect, last_coherence should be set
    assert eng.last_coherence is not None
    # Council should still return a valid result
    assert "recommendation" in result
    assert "voices" in result
    assert len(result["voices"]) == 4


def test_council_does_not_reflect_when_coherence_exists(tmp_path):
    """Council skips auto-reflect when last_coherence already set."""
    from conscio import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name="test", storage_path=str(tmp_path))
    # Manually run reflect first
    eng.reflect()
    assert eng.last_coherence is not None
    coherence_before = eng.last_coherence
    result = council(eng, question="Should we proceed?")
    # Coherence should be unchanged (no re-reflect)
    assert eng.last_coherence is coherence_before
    assert "recommendation" in result
