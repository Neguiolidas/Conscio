# tests/test_agency_state.py
"""Lockdown persistence on ConsciousnessState + ModelInfo agentic flags
(blueprint sections 5 and 6 — literal requirements)."""
from conscio.context_manager import ConsciousnessState, ContextManager
from conscio.models import ContextMode, ModelInfo


class TestLockdownField:
    def test_defaults_to_false(self):
        assert ConsciousnessState().action_lockdown is False

    def test_roundtrip_through_save_and_load(self, tmp_path):
        manager = ContextManager("glm-5.1", storage_path=tmp_path)
        state = ConsciousnessState(state_summary="s", action_lockdown=True)
        manager.save_state(state)
        loaded = manager.load_state()
        assert loaded.action_lockdown is True

    def test_legacy_payload_without_key_loads_false(self, tmp_path):
        manager = ContextManager("glm-5.1", storage_path=tmp_path)
        manager.save_state(ConsciousnessState(state_summary="old"))
        # simulate a pre-v1.0 file: strip the key from the saved JSON
        import json
        path = tmp_path / "state_summary.json"
        data = json.loads(path.read_text())
        data.pop("action_lockdown", None)
        path.write_text(json.dumps(data))
        assert manager.load_state().action_lockdown is False


class TestModelInfoFlags:
    def test_flags_default_false(self):
        info = ModelInfo(name="m", context_window=128000,
                         mode=ContextMode.COMPACT)
        assert info.has_json_mode is False
        assert info.supports_gbnf is False

    def test_flags_settable(self):
        info = ModelInfo(name="m", context_window=128000,
                         mode=ContextMode.COMPACT, has_json_mode=True,
                         supports_gbnf=True)
        assert info.has_json_mode and info.supports_gbnf
