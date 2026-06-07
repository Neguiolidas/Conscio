# tests/test_self_prompt_state.py
from conscio.context_manager import ContextManager, ConsciousnessState
from conscio.models import ContextMode


def test_state_fields_roundtrip(tmp_path):
    cm = ContextManager("glm-5.1", storage_path=tmp_path)
    st = cm.build_state(
        state_summary="x",
        self_prompt="why do I hold contradictory world-model assertions?",
        dream_recommended="recommended (ontological 0.30)",
    )
    cm.save_state(st)
    loaded = cm.load_state()
    assert loaded.self_prompt == "why do I hold contradictory world-model assertions?"
    assert loaded.dream_recommended == "recommended (ontological 0.30)"


def test_injection_renders_markers(tmp_path):
    cm = ContextManager("glm-5.1", storage_path=tmp_path)
    st = cm.build_state(state_summary="x", self_prompt="why?",
                        dream_recommended="recommended (ontological 0.30)")
    inj = st.to_injection()
    assert "❓ self-prompt: why?" in inj
    assert "☾ dream: recommended (ontological 0.30)" in inj


def test_minimal_mode_omits_markers():
    st = ConsciousnessState(self_prompt="why?", dream_recommended="r",
                            context_mode=ContextMode.MINIMAL)
    inj = st.to_injection()
    assert "self-prompt" not in inj and "dream:" not in inj


def test_empty_fields_omitted():
    st = ConsciousnessState(context_mode=ContextMode.STANDARD)
    inj = st.to_injection()
    assert "self-prompt" not in inj and "☾ dream" not in inj
