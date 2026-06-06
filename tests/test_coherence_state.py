# tests/test_coherence_state.py
from conscio.context_manager import ContextManager


def _cm(tmp_path):
    return ContextManager("glm-5.1", context_window=131000, storage_path=str(tmp_path))


def test_render_coherence_marker(tmp_path):
    state = _cm(tmp_path).build_state(
        state_summary="x", coherence=0.41, coherence_note="epistemic")
    assert "▷ coherence: 0.41 dominant: epistemic" in state.to_injection()


def test_render_coherence_no_dominant(tmp_path):
    state = _cm(tmp_path).build_state(state_summary="x", coherence=0.82)
    inj = state.to_injection()
    assert "▷ coherence: 0.82" in inj
    assert "dominant:" not in inj


def test_render_coherence_suppressed_when_none(tmp_path):
    state = _cm(tmp_path).build_state(state_summary="x")
    assert "coherence:" not in state.to_injection()


def test_render_voice_marker(tmp_path):
    state = _cm(tmp_path).build_state(state_summary="x", voice="coherence-style")
    assert "⊙ voice: coherence-style" in state.to_injection()


def test_save_load_round_trips_coherence_and_voice(tmp_path):
    cm = _cm(tmp_path)
    state = cm.build_state(state_summary="x", coherence=0.41,
                           coherence_note="epistemic", voice="coherence-style")
    cm.save_state(state)
    loaded = cm.load_state()
    assert loaded.coherence == 0.41
    assert loaded.coherence_note == "epistemic"
    assert loaded.voice == "coherence-style"
